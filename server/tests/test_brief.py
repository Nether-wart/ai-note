"""简报：生成、**数字闸门**、落盘、过期（#17 §7、契约 §10.5）。

这批测试盯的是一条**硬闸门**：简报里引用的每一个数字都必须能在本次索引里逐字找回。
最要命的失败是一段读起来很顺、**数字却是编的**总结——它没法用眼睛查，所以只能自动查死：
`path` 解不出来是**对不上**（不是跳过）、值对上了但种类不对也是**对不上**；只要有一条
对不上就**一个字节都不落盘**（连 `briefs/` 目录都不许建出来）。

模型调用一律走**注入的假 client**：不联网、不花钱（仓库的规矩）。闸门本身是纯函数，
可以不碰模型直接喂。`path` 语法见 `server/brief.py` 的模块 docstring——这一份是实现，
测试逐条钉住它。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from conftest import make_card, make_data_dir

from server import brief, config, errors
from server.errors import ApiError
from server.model_client import ModelUnavailable

NOW = datetime(2026, 10, 4, 9, 39, 57, tzinfo=timezone.utc)
COVERS = "2026-10-04T09:39:57+00:00"

# 纯闸门测试用的手造索引：只放被引用的路径，形状照 `/api/index` 的 `data`。
INDEX = {
    "built_at": COVERS,
    "problems": [
        {"id": "p-1", "subject": "数学", "created_at": "2026-10-01T08:00:00+08:00",
         "mastery_cn": "在池", "error_causes": ["概念不清"]},
        {"id": "p-2", "subject": "数学", "created_at": "2020-01-01T00:00:00+08:00",
         "mastery_cn": "毕业", "error_causes": []},
        {"id": "p-3", "subject": None, "created_at": "2026-10-02T00:00:00+00:00",
         "mastery_cn": "在池"},
    ],
    "stats": {
        "by_subject": {"数学": {"problems": 2, "in_default_list": 1, "cooling": 1,
                                "graduated": 1, "auto_judge_eligible": 1, "unreviewed": 0}},
        "unclassified": 1,
    },
}


def _catalog(root, now=NOW):
    from server.catalog import Catalog

    return Catalog(root, clock=lambda: now)


def echo_client(*, text=None, drop_unclassified=False, **identity):
    """一个**诚实**的假 client：把候选读数原样抄回去（那是索引里的真值）。

    `drop_unclassified=True` 造出「正文说了、facts 里却没有未归类那条」的漏项。
    """
    def call(subject, digest):
        history = [dict(fact) for fact in digest["history_facts"]]
        if drop_unclassified:
            history = [f for f in history if f["path"] != brief.UNCLASSIFIED_PATH]
        unclassified = next((f["value"] for f in digest["history_facts"]
                             if f["path"] == brief.UNCLASSIFIED_PATH), 0)
        return {
            "parsed": True,
            "text": text or f"最近 7 天没什么动静。另有 {unclassified} 道未归类未计入。",
            "window_facts": [dict(fact) for fact in digest["window_facts"]],
            "history_facts": history,
            "provider": "fake",
            "model": "fake-brief",
            "run_id": "20261004-093957-000-brief.json",
            **identity,
        }
    return call


def fabricated_client(value=999, path="stats.by_subject.数学.cooling"):
    """一个**编数字**的假 client：未归类那条是真话，冷却道数是编的。"""
    def call(subject, digest):
        return {
            "parsed": True,
            "text": f"冷却中有 {value} 道。另有 0 道未归类未计入。",
            "window_facts": [],
            "history_facts": [
                {"label": brief.UNCLASSIFIED_LABEL, "path": brief.UNCLASSIFIED_PATH,
                 "value": 0},
                {"label": "编出来的冷却道数", "path": path, "value": value},
            ],
        }
    return call


# ------------------------------------------------------------------ 闸门（纯函数）


def test_a_true_fact_passes_the_gate():
    result = brief.verify_facts(
        [{"label": "冷却中的道数", "path": "stats.by_subject.数学.cooling", "value": 1}],
        INDEX,
    )

    assert result == {"ok": True, "mismatches": []}


def test_a_number_the_index_does_not_contain_is_a_mismatch():
    result = brief.verify_facts(
        [{"label": "冷却中的道数", "path": "stats.by_subject.数学.cooling", "value": 999}],
        INDEX,
    )

    assert result["ok"] is False
    assert result["mismatches"] == [
        {"label": "冷却中的道数", "path": "stats.by_subject.数学.cooling",
         "claimed": 999, "actual": 1, "reason": "value_mismatch"}
    ]


def test_a_path_that_does_not_resolve_is_a_mismatch_not_a_skip():
    """解不出来要**报**出来——不是当它没写、更不是拿 `null` 冒充。"""
    missing_key = brief.verify_facts(
        [{"label": "不存在", "path": "stats.by_subject.数学.nope", "value": 1}], INDEX)

    assert missing_key["ok"] is False
    assert missing_key["mismatches"][0]["reason"] == "path_unresolved"
    assert missing_key["mismatches"][0]["actual"] is None

    # 下标越界同样是解不出来（不许静默取最后一条）
    out_of_range = brief.verify_facts(
        [{"label": "越界", "path": "problems[99].mastery_cn", "value": "在池"}], INDEX)
    assert out_of_range["mismatches"][0]["reason"] == "path_unresolved"


def test_a_kind_mismatch_is_a_mismatch():
    """字符串 `"1"` 与数字 `1` 不是同一个东西——种类先相等，再谈值。"""
    result = brief.verify_facts(
        [{"label": "冷却", "path": "stats.by_subject.数学.cooling", "value": "1"}], INDEX)

    assert result["mismatches"] == [
        {"label": "冷却", "path": "stats.by_subject.数学.cooling",
         "claimed": "1", "actual": 1, "reason": "kind_mismatch"}
    ]


def test_numbers_compare_numerically_and_other_kinds_strictly():
    index = {"n": 2, "s": "在池", "b": True, "none": None, "list": ["概念不清"]}

    def ok(fact):
        return brief.verify_facts([fact], index)["ok"]

    assert ok({"path": "n", "value": 2.0, "label": "x"}) is True, "2 与 2.0 是同一个数"
    assert ok({"path": "s", "value": "在池", "label": "x"}) is True
    assert ok({"path": "none", "value": None, "label": "x"}) is True
    assert ok({"path": "list", "value": ["概念不清"], "label": "x"}) is True
    # 布尔不许冒充数字（Python 里 True == 1）
    assert ok({"path": "b", "value": 1, "label": "x"}) is False
    # 字符串是**逐字**比：多一个空格就是另一个字符串
    assert ok({"path": "s", "value": "在池 ", "label": "x"}) is False


def test_the_path_grammar_is_exactly_the_one_in_the_docstring():
    """点号分段 + `[n]` 下标；中文键逐字匹配、不做转义。"""
    assert brief.resolve_path("stats.by_subject.数学.cooling", INDEX) == (True, 1)
    assert brief.resolve_path("stats.unclassified", INDEX) == (True, 1)
    assert brief.resolve_path("problems[0].mastery_cn", INDEX) == (True, "在池")
    assert brief.resolve_path("problems[2].mastery_cn", INDEX) == (True, "在池")
    assert brief.resolve_path("problems[0].error_causes", INDEX) == (True, ["概念不清"])
    assert brief.resolve_path("problems[0].error_causes[0]", INDEX) == (True, "概念不清")

    # 表外科目的桶不存在 → 解不出来（不是「当作 0」）
    assert brief.resolve_path("stats.by_subject.物理.cooling", INDEX) == (False, None)
    # 对数组取键、对对象取下标：类型不对就是解不出来
    assert brief.resolve_path("problems.mastery_cn", INDEX) == (False, None)
    assert brief.resolve_path("stats[0]", INDEX) == (False, None)
    # 空段、空路径都不合法
    assert brief.resolve_path("stats..unclassified", INDEX) == (False, None)
    assert brief.resolve_path("", INDEX) == (False, None)
    assert brief.resolve_path(None, INDEX) == (False, None)


def test_a_malformed_fact_is_a_mismatch_too():
    """没有 `value` 键 = 没有「声称的值」可比；不是对象更不算一条可核对的事实。"""
    assert brief.verify_facts([{"path": "stats.unclassified"}], INDEX)[
        "mismatches"][0]["reason"] == "missing_value"
    assert brief.verify_facts(["这不是一条事实"], INDEX)[
        "mismatches"][0]["reason"] == "bad_fact"


# ------------------------------------------------------------------ 生成：编数字


def test_a_fabricated_number_refuses_to_store_anything(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    with pytest.raises(ApiError) as excinfo:
        brief.generate(catalog, "数学", client=fabricated_client())

    error = excinfo.value
    assert (error.status, error.code, error.reason) == (
        502, "brief_unverifiable", "brief_unverifiable")
    # 对不上的逐条列出来：谁、哪个路径、声称什么、实际什么
    assert [f["path"] for f in error.details["facts"]] == ["stats.by_subject.数学.cooling"]
    assert error.details["facts"][0]["claimed"] == 999
    assert error.details["facts"][0]["actual"] == 0
    # 「不落盘」是判据的一部分：连 briefs/ 目录都不许建出来（契约 §12.1）
    assert not (root / "briefs").exists()


def test_an_answer_with_nothing_to_check_is_the_same_502(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    def not_json(subject, digest):
        return {"parsed": False, "text": None, "window_facts": None,
                "history_facts": None, "reason": "响应里没有 JSON：模型说了一段话"}

    with pytest.raises(ApiError) as excinfo:
        brief.generate(catalog, "数学", client=not_json)

    assert excinfo.value.code == "brief_unverifiable"
    assert excinfo.value.details["facts"] == []
    # 原话照带（不许只说「解析失败」）
    assert "没有 JSON" in excinfo.value.details["note"]
    assert not (root / "briefs").exists()

    # 连对象都不是：同样退化成一条说得清的 502
    with pytest.raises(ApiError) as excinfo:
        brief.generate(catalog, "数学", client=lambda subject, digest: "一段散文")
    assert excinfo.value.details["facts"] == []
    assert "不是一个对象" in excinfo.value.details["note"]


def test_a_failed_call_is_not_wrapped_into_the_gate_error(tmp_path):
    """「模型没问成」原样往上抛：它与「数字编了」处置完全不同（一个能重试、一个不能）。"""
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    def dead(subject, digest):
        raise ModelUnavailable("上游挂了")

    with pytest.raises(ModelUnavailable):
        brief.generate(catalog, "数学", client=dead)
    assert not (root / "briefs").exists()


def test_the_gate_error_says_retrying_will_not_help():
    """hint 要把「重试无用」说明白——与 `model_unavailable` 的「可以直接重试」相反。"""
    raised = errors.brief_unverifiable(
        "数学", [{"label": "x", "path": "p", "claimed": 1, "actual": 0,
                  "reason": "value_mismatch"}])

    assert (raised.status, raised.code) == (502, "brief_unverifiable")
    assert "重试无用" in raised.hint


def test_a_bad_window_days_is_refused_before_any_model_call(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)
    calls: list = []

    for bad in (0, -1, 7.0, True, "7"):
        with pytest.raises(ApiError) as excinfo:
            brief.generate(catalog, "数学", client=lambda *a: calls.append(a), window_days=bad)
        assert (excinfo.value.status, excinfo.value.reason) == (400, "bad_request")
        assert excinfo.value.details["param"] == "window_days"

    assert calls == [], "参数错要在问模型之前拒掉（不花钱）"
    assert not (root / "briefs").exists()


# ------------------------------------------------------------------ 生成：诚实的回答


def test_an_honest_answer_is_written_and_round_trips(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    result = brief.generate(catalog, "数学", client=echo_client())

    path = root / "briefs" / "数学-2026-10-04.json"
    assert result["path"] == str(path)
    assert result["run_id"] == "20261004-093957-000-brief.json"
    assert path.is_file()

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == result["brief"]
    # 形状就是契约 §10.5 的那几个键，一个不多一个不少
    assert set(on_disk) == {
        "subject", "generated_at", "window_days", "window_from", "window_until",
        "covers_until", "provider", "model", "text", "window_facts", "history_facts",
    }
    assert on_disk["subject"] == "数学"
    assert on_disk["window_days"] == 7
    assert on_disk["window_from"] == "2026-09-28"      # 含端点的 7 天
    assert on_disk["window_until"] == "2026-10-04"
    assert on_disk["covers_until"] == catalog.index()[0]["built_at"]
    assert on_disk["generated_at"] == "2026-10-04T09:39:57+00:00"
    assert (on_disk["provider"], on_disk["model"]) == ("fake", "fake-brief")
    assert on_disk["text"]
    assert all(set(fact) == {"label", "path", "value"}
               for fact in on_disk["window_facts"] + on_disk["history_facts"])

    # 读回来的就是落盘的那一份（load_latest 返回 (brief, path)）
    loaded, loaded_path = brief.load_latest(catalog, "数学")
    assert loaded == on_disk and loaded_path == path

    # 一天一份：同一天再生成覆盖同一份，不新增文件
    brief.generate(catalog, "数学", client=echo_client())
    assert [p.name for p in (root / "briefs").iterdir()] == ["数学-2026-10-04.json"]


def test_the_window_candidates_only_cover_what_was_recorded_inside_the_window(tmp_path):
    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa"),                        # 2020 年，不在窗口里
        make_card("p-20261001-bbbbbb", **{"created_at": "2026-10-01T08:00:00+08:00"}),
    ])
    catalog = _catalog(root)
    index = catalog.index()[0]

    digest = brief.brief_digest(index, "数学")

    assert digest["window_from"] == "2026-09-28" and digest["window_until"] == "2026-10-04"
    window_paths = [fact["path"] for fact in digest["window_facts"]]
    # 窗口里那道题在索引里的位置是 1（索引按 sort_key 排过），它自己的读数就在那儿
    assert "problems[1].mastery_cn" in window_paths
    assert all(not path.startswith("problems[0].") for path in window_paths)
    # 窗口候选本身也必须条条可解（递一把注定对不上的枪给模型是设计错，不是模型错）
    assert brief.verify_facts(digest["window_facts"], index)["ok"] is True
    assert brief.verify_facts(digest["history_facts"], index)["ok"] is True


def test_unclassified_cards_are_left_out_of_the_subject_and_counted_in_history_facts(tmp_path):
    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa"),
        make_card("p-20200101-bbbbbb"),
        make_card("p-20200101-cccccc", **{"subject": None}),
    ])
    catalog = _catalog(root)

    result = brief.generate(catalog, "数学", client=echo_client())

    facts = {fact["path"]: fact["value"] for fact in result["brief"]["history_facts"]}
    assert facts["stats.by_subject.数学.problems"] == 2, "未归类那道不算进任何科目"
    assert facts["stats.unclassified"] == 1, "但它也不许消失：N 摆在全部历史那一组里"
    assert "另有 1 道未归类未计入" in result["brief"]["text"]


def test_a_brief_that_omits_the_unclassified_count_is_refused(tmp_path):
    """正文说了、`history_facts` 里没有那一条 → 拒。N 必须过同一道闸门。"""
    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa"),
        make_card("p-20200101-bbbbbb", **{"subject": None}),
    ])
    catalog = _catalog(root)

    with pytest.raises(ApiError) as excinfo:
        brief.generate(catalog, "数学", client=echo_client(drop_unclassified=True))

    assert excinfo.value.code == "brief_unverifiable"
    assert excinfo.value.details["facts"] == [
        {"label": brief.UNCLASSIFIED_LABEL, "path": brief.UNCLASSIFIED_PATH,
         "claimed": None, "actual": 1, "reason": "missing_fact"}
    ]
    assert not (root / "briefs").exists()


# ------------------------------------------------------------------ 读与落盘


def test_no_brief_at_all_is_a_404_brief_missing(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    with pytest.raises(ApiError) as excinfo:
        brief.load_latest(catalog, "数学")

    error = excinfo.value
    assert (error.status, error.code, error.reason) == (404, "not_found", "brief_missing")
    assert error.details["subject"] == "数学"
    assert error.details["available"] == []


def test_load_at_matches_the_date_in_the_file_name_verbatim(tmp_path):
    """`?at=` 不落到「最接近的一天」：那天没有就是 404，并把**实际有哪几天**说出来。"""
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)
    brief.generate(catalog, "数学", client=echo_client())

    with pytest.raises(ApiError) as excinfo:
        brief.load_at(catalog, "数学", "2026-10-03")

    assert excinfo.value.reason == "brief_missing"
    assert excinfo.value.details["at"] == "2026-10-03"
    assert excinfo.value.details["available"] == ["2026-10-04"]

    loaded, path = brief.load_at(catalog, "数学", "2026-10-04")
    assert loaded["subject"] == "数学" and path.name == "数学-2026-10-04.json"


def test_list_briefs_only_counts_this_subject_and_only_well_formed_dates(tmp_path):
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)
    brief.generate(catalog, "数学", client=echo_client())
    directory = root / "briefs"
    (directory / "数学-2026-10-01.json").write_text("{}", encoding="utf-8")
    (directory / "数学-上周.json").write_text("{}", encoding="utf-8")          # 日期形状不对
    (directory / "物理-2026-10-02.json").write_text("{}", encoding="utf-8")     # 别的科目

    assert brief.list_briefs(catalog, "数学") == ["2026-10-01", "2026-10-04"]
    assert brief.list_briefs(catalog, "物理") == ["2026-10-02"]
    assert brief.list_briefs(catalog, "化学") == []


def test_a_file_that_is_there_but_unreadable_is_not_a_404(tmp_path):
    """「还没有」与「读不到」是两件事：文件在、内容坏 → 500，不是 404。"""
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)
    (root / "briefs").mkdir()
    (root / "briefs" / "数学-2026-10-04.json").write_text("{ 这不是 JSON", encoding="utf-8")

    with pytest.raises(ApiError) as excinfo:
        brief.load_latest(catalog, "数学")

    assert excinfo.value.status == 500
    assert excinfo.value.reason == "filesystem_error"


def test_a_subject_or_date_with_path_parts_never_reaches_the_disk(tmp_path):
    """内容不许决定写到哪、读到哪（与 `page_id_mismatch` 同一类纪律）。"""
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    with pytest.raises(ApiError) as excinfo:
        brief.load_at(catalog, "../problems/p-20200101-aaaaaa", "2026-10-04")
    assert (excinfo.value.status, excinfo.value.details["param"]) == (400, "subject")

    with pytest.raises(ApiError) as excinfo:
        brief.load_at(catalog, "数学", "../../etc/passwd")
    assert (excinfo.value.status, excinfo.value.details["param"]) == (400, "at")


# ------------------------------------------------------------------ 过期


def test_stale_counts_exactly_the_cards_recorded_after_covers_until(tmp_path):
    root = make_data_dir(tmp_path, [])
    catalog = _catalog(root)
    document = {"subject": "数学", "covers_until": COVERS}
    records = [
        {"subject": "数学", "created_at": "2026-10-04T10:00:00+00:00"},   # 晚一刻 → 新
        {"subject": "数学", "created_at": "2026-10-04T18:00:00+08:00"},   # = 10:00Z → 新
        {"subject": "数学", "created_at": "2026-10-04T08:00:00+08:00"},   # = 00:00Z → 旧
        # 字符串比会把它算成新的（"2026-10-04T08:00:00+08:00" > "2026-10-04T09:39:57+00:00"），
        # 归一化到 UTC 后它是当天 00:00Z，比快照早——正是这一条钉住「比时刻不比字符串」
        {"subject": "数学", "created_at": "2026-10-04T09:39:57+00:00"},   # 相等 → 不算新
        {"subject": "物理", "created_at": "2026-10-05T00:00:00+00:00"},   # 别的科目
        {"subject": "数学", "created_at": None},                          # 读不出来 → 无法比较
    ]

    assert brief.stale(catalog, "数学", document, records) == {
        "stale": True, "new_problems": 2}

    fresh = {"subject": "数学", "covers_until": "2026-10-05T00:00:00+00:00"}
    assert brief.stale(catalog, "数学", fresh, records) == {
        "stale": False, "new_problems": 0}


def test_stale_without_records_reads_the_index_itself(tmp_path):
    root = make_data_dir(tmp_path, [make_card("p-20200101-aaaaaa")])
    catalog = _catalog(root)

    assert brief.stale(catalog, "数学", {"covers_until": COVERS}) == {
        "stale": False, "new_problems": 0}
    assert brief.stale(catalog, "数学", {"covers_until": "2019-01-01T00:00:00+00:00"}) == {
        "stale": True, "new_problems": 1}


def test_a_brief_whose_covers_until_is_unreadable_reports_stale(tmp_path):
    """读不出依据哪一刻 → 按过期报：宁可让人重新生成一次，也别端着它说「它是新的」。"""
    root = make_data_dir(tmp_path, [make_card()])
    catalog = _catalog(root)

    assert brief.stale(catalog, "数学", {"covers_until": None}) == {
        "stale": True, "new_problems": 0}


# ------------------------------------------------------------------ 配置


def test_the_brief_role_defaults_to_the_extract_defaults():
    cfg = config.load_role_config(config.BRIEF_ROLE, {})

    assert cfg.role == "brief"
    assert (cfg.provider, cfg.model) == ("deepseek", "deepseek-flash")
    assert config.ROLE_DEFAULTS["brief"] == config.ROLE_DEFAULTS["extract"]


def test_the_brief_role_has_its_own_provider_and_model_env_vars():
    cfg = config.load_role_config(config.BRIEF_ROLE, {
        "BRIEF_PROVIDER": "dashscope", "BRIEF_MODEL": "qwen-max"})

    assert (cfg.provider, cfg.model) == ("dashscope", "qwen-max")
    assert cfg.key_env == "DASHSCOPE_API_KEY"


def test_an_undefined_brief_provider_is_a_load_time_error():
    # ADR 0010 之后不再有白名单；但**没定义完整**的名字仍然是装载期错误，
    # 而且报错要点出该定义哪两个变量、以及两家预设叫什么。
    with pytest.raises(ValueError) as excinfo:
        config.load_role_config(config.BRIEF_ROLE, {"BRIEF_PROVIDER": "openai"})

    message = str(excinfo.value)
    assert "BRIEF_PROVIDER" in message
    assert "openai" in message
    assert "AI_NOTE_PROVIDER_OPENAI_BASE_URL" in message
    assert "dashscope" in message and "deepseek" in message


# ------------------------------------------------------------------ 调用接缝


class FakeTransport:
    """按顺序吐预先排好的 `(status, text)`（形状与 `test_judge_client.py` 的那个一样）。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": headers, "payload": payload,
                           "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_the_http_brief_client_asks_for_json_and_archives_the_run(tmp_path):
    from server.brief_client import BRIEF_TAG, HttpBrief

    answer = {"text": "另有 0 道未归类未计入。", "window_facts": [],
              "history_facts": [{"label": "未归类未计入", "path": "stats.unclassified",
                                 "value": 0}]}
    body = json.dumps({"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}],
                       "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
                      ensure_ascii=False)
    transport = FakeTransport((200, body))
    client = HttpBrief(
        config.load_role_config(config.BRIEF_ROLE, {}), tmp_path / "runs",
        transport=transport, sleep=lambda _s: None, env={"DEEPSEEK_API_KEY": "test-key"},
        clock=lambda: NOW,
    )

    parsed = client("数学", {"window_days": 7, "window_from": "2026-09-28",
                             "window_until": "2026-10-04", "window_facts": [],
                             "history_facts": []})

    assert parsed["parsed"] is True
    assert parsed["history_facts"] == answer["history_facts"]
    assert (parsed["provider"], parsed["model"]) == ("deepseek", "deepseek-flash")
    assert parsed["run_id"].endswith(f"-{BRIEF_TAG}.json")
    assert (tmp_path / "runs" / parsed["run_id"]).is_file()
    # 纯文本角色：**一张图都不发**（与切分／抽取角色相反）
    messages = transport.calls[0]["payload"]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert all(isinstance(m["content"], str) for m in messages)
    assert "另有 N 道未归类未计入" in messages[0]["content"]


def test_the_real_client_and_the_gate_work_together(tmp_path):
    """真接缝走一遍：提示词里的候选读数 → 模型原文 → 闸门 → 落盘。

    假 transport 从**模型真能看到的那条 user 消息**里把候选读数读出来，照它写一份合格回答
    ——闸门要核对的正是这些数字从提示词到落盘的一整条路。
    """
    from server.brief_client import HttpBrief

    root = make_data_dir(tmp_path, [
        make_card(),
        make_card("p-20200101-bbbbbb", **{"subject": None}),
    ])
    catalog = _catalog(root)

    def transport(url, headers, payload, timeout):
        user = payload["messages"][1]["content"]
        digest = json.loads(user[user.index("{"):])
        by_path = {fact["path"]: fact for fact in digest["history_facts"]}
        chosen = [by_path["stats.by_subject.数学.problems"],
                  by_path[brief.UNCLASSIFIED_PATH]]
        answer = {
            "text": f"这个科目共 {chosen[0]['value']} 道题。"
                    f"另有 {chosen[1]['value']} 道未归类未计入。",
            "window_facts": [],
            "history_facts": chosen,
        }
        body = {"choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}}]}
        return 200, json.dumps(body, ensure_ascii=False)

    client = HttpBrief(
        config.load_role_config(config.BRIEF_ROLE, {}), tmp_path / "runs",
        transport=transport, sleep=lambda _s: None, env={"DEEPSEEK_API_KEY": "test-key"},
        clock=lambda: NOW,
    )

    result = brief.generate(catalog, "数学", client=client)

    assert result["brief"]["history_facts"] == [
        {"label": "数学全部题数", "path": "stats.by_subject.数学.problems", "value": 1},
        {"label": brief.UNCLASSIFIED_LABEL, "path": brief.UNCLASSIFIED_PATH, "value": 1},
    ]
    assert (root / "briefs" / "数学-2026-10-04.json").is_file()
    assert (tmp_path / "runs" / result["run_id"]).is_file()
