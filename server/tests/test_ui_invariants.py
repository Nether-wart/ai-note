"""屏幕重做的界面不变量（#8）：**从 pytest 直接驱动界面那一侧的判据**。

为什么要有这份桥（照 #7 `test_site_redo_queue.py` 的做法）：判据住在
`site/tests/invariants.mjs`，是**一行 Python 也没有**的纯逻辑。把「题面必须是擦除图」
「越界必须失败」这些规则在 Python 里再抄一遍，才是这个项目反复被咬的失败
（两处各判各的）。所以这里用 node 当探针跑**同一份判据**。

这份文件管三件事：

1. **四条不变量各自的报警能力**（`test_ui_invariants_alarm_on_bad_input` 与它的好输入
   对照）——用探针喂代表性 HTML，脱网、不装依赖也能跑。
2. **自检命令自己**（`node site/tests/selftest.mjs`）：它对**真组件渲出来的 HTML**
   跑同一批判据，并把判据对着故意做坏的夹具再验一次。要 `site/node_modules`。
3. **自检不得有副作用**：在**真跑子进程的前后**各拍一张目录快照来比。原型的
   `--selftest` 会写 `data/index.json`（`proto/server.py:973 → :637 → :172`），
   所以这一条既要有"它真的没写"的证据，也要有"写了会被抓住"的证据——
   后者用 `site/tests/fixtures/legacy-selftest.mjs`（照原型形状重写的替身）。

没有 node 就整份 skip：界面不变量不是后端的事。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SITE_TESTS = ROOT / "site" / "tests"
SELFTEST = SITE_TESTS / "selftest.mjs"
PROBE = SITE_TESTS / "probe-invariants.mjs"
SNAPSHOT = SITE_TESTS / "snapshot-tree.mjs"
LEGACY = SITE_TESTS / "fixtures" / "legacy-selftest.mjs"
NODE_MODULES = ROOT / "site" / "node_modules"

HAS_NODE = shutil.which("node") is not None
HAS_RENDER_DEPS = (NODE_MODULES / "react-dom").exists() and (NODE_MODULES / "@babel" / "core").exists()

pytestmark = pytest.mark.skipif(
    not HAS_NODE, reason="没有 node：界面不变量的判据住在 site/tests/invariants.mjs 里"
)

needs_render = pytest.mark.skipif(
    not HAS_RENDER_DEPS,
    reason="没有 site/node_modules：真组件的渲染自检跑不了（装：npm_config_cache=\"$PWD/.npm-cache\" npm ci）",
)


def run_node(args, *, cwd=ROOT, env=None, timeout=180):
    return subprocess.run(
        ["node", *map(str, args)],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={**os.environ, **(env or {})},
        timeout=timeout,
    )


def probe(fn: str, *args):
    """跑一次 `site/tests/invariants.mjs` 里的判据，拿回违规数组。"""
    payload = json.dumps([{"fn": fn, "args": list(args)}], ensure_ascii=False)
    done = subprocess.run(
        ["node", str(PROBE)], input=payload, capture_output=True, text=True, cwd=str(ROOT), timeout=120
    )
    assert done.returncode == 0, f"node 探针失败：{done.stderr}"
    return json.loads(done.stdout)[0]


def snapshot(*roots):
    """拍一张目录快照（`snapshot-tree.mjs` 只采不判，判在 invariants.mjs 里）。"""
    done = run_node([SNAPSHOT, *roots])
    assert done.returncode == 0, f"拍快照失败：{done.stderr}"
    return json.loads(done.stdout)


def codes(violations):
    return sorted(v["code"] for v in violations)


# --------------------------------------------------------------- 夹具

CLEAN = "/api/problem/p-a/image/clean"
CHOICE_PROBLEM = {
    "id": "p-a",
    "type": "choice",
    "options": [{"label": "A", "text": "甲"}],
    "auto_judge": {"eligible": True, "reason": None, "reason_text": None},
}
SOLUTION_PROBLEM = {
    "id": "p-b",
    "type": "solution",
    "options": [],
    "standard_answer": "见正解",
    "correct_solution": "标准解法：先证充分性，再证必要性。",
    "auto_judge": {
        "eligible": False,
        "reason": "solution_type",
        "reason_text": "解答题只能人工确认：过程题在屏幕上敲不出过程",
    },
}
CHOICE_FORM = (
    '<article data-current-pid="p-a" data-answer-mode="choice">'
    '<form data-answer-form="choice"><fieldset data-answer-input="choice">'
    '<input type="radio" name="answer" value="A"/></fieldset></form></article>'
)
SOLUTION_TEXTAREA = (
    '<article data-current-pid="p-b" data-answer-mode="fillin">'
    '<textarea name="answer"></textarea></article>'
)
QUEUE_PROBLEMS = [{"id": "p-a"}, {"id": "p-b"}]


# -------------------------------- 四条不变量：好输入通过（不报警）

GOOD_INPUTS = [
    (
        "1_题面是擦除图",
        "checkQuestionImage",
        [{"html": f'<article data-current-pid="p-a"><img src="{CLEAN}" data-image-kind="clean"/></article>',
          "pid": "p-a"}],
    ),
    ("2_解答题不给作答框", "checkAnswerBox", [{"html": '<article data-current-pid="p-b" data-answer-mode="none">'
                                                      '<div data-answer-blocked="solution_type">不给框</div></article>',
                                                "problem": SOLUTION_PROBLEM}]),
    ("3_队列在范围内", "checkQueue", [{"html": '<article data-current-pid="p-b"></article>',
                                       "search": "?queue=p-a,p-b&i=1", "problems": QUEUE_PROBLEMS}]),
    ("3_越界渲染成失败面板", "checkQueue", [{"html": '<div data-error-code="index_out_of_range"></div>',
                                              "search": "?queue=p-a,p-b&i=9", "problems": QUEUE_PROBLEMS}]),
    ("4_自检什么都没写", "checkNoSideEffects", [
        {"before": {"files": {"data/problems/p-a.json": {"size": 1, "mtime_ns": 1}}},
         "after": {"files": {"data/problems/p-a.json": {"size": 1, "mtime_ns": 1}}}}]),
    ("5_页面上没有答案", "checkNoAnswerLeak", [{"html": CHOICE_FORM, "problem": CHOICE_PROBLEM}]),
]


@pytest.mark.parametrize("label,fn,args", GOOD_INPUTS, ids=[case[0] for case in GOOD_INPUTS])
def test_ui_invariants_pass_on_good_input(label, fn, args):
    assert probe(fn, *args) == [], f"{label}：好输入不该报警"


# -------------------------------- 四条不变量：坏输入**必须报警**

BAD_INPUTS = [
    (
        "1_题面被换成原图",
        "checkQuestionImage",
        [{"html": '<article data-current-pid="p-a">'
                  '<img src="/api/problem/p-a/image/original" data-image-kind="clean"/></article>',
          "pid": "p-a"}],
        "question_image_not_clean",
    ),
    (
        "1_题面一张图都没有",
        "checkQuestionImage",
        [{"html": '<article data-current-pid="p-a"><div>题干</div></article>', "pid": "p-a"}],
        "question_image_missing",
    ),
    (
        "2_解答题出现了作答框",
        "checkAnswerBox",
        [{"html": SOLUTION_TEXTAREA, "problem": SOLUTION_PROBLEM}],
        "solution_has_answer_box",
    ),
    (
        "2_该有框的题没框",
        "checkAnswerBox",
        [{"html": '<article data-current-pid="p-a" data-answer-mode="choice"></article>',
          "problem": CHOICE_PROBLEM}],
        "answer_box_missing",
    ),
    (
        "3_越界悄悄落到第一题",
        "checkQueue",
        [{"html": '<article data-current-pid="p-a"></article>',
          "search": "?queue=p-a,p-b&i=9", "problems": QUEUE_PROBLEMS}],
        "fell_back_to_the_first_problem",
    ),
    (
        "3_越界却什么失败都没有",
        "checkQueue",
        [{"html": '<div data-redo-page="true"></div>',
          "search": "?queue=p-a,p-b&i=9", "problems": QUEUE_PROBLEMS}],
        "queue_failure_not_rendered",
    ),
    (
        "4_自检生成了派生索引",
        "checkNoSideEffects",
        [{"before": {"files": {"data/problems/p-a.json": {"size": 1, "mtime_ns": 1}}},
          "after": {"files": {"data/problems/p-a.json": {"size": 1, "mtime_ns": 1},
                              "data/index.json": {"size": 2, "mtime_ns": 2}}}}],
        "selftest_created_file",
    ),
    (
        "5_页面上出现了正解文本",
        "checkNoAnswerLeak",
        [{"html": '<article data-current-pid="p-b">标准解法：先证充分性，再证必要性。</article>',
          "problem": SOLUTION_PROBLEM}],
        "answer_text_leaked",
    ),
]


@pytest.mark.parametrize("label,fn,args,expected", BAD_INPUTS, ids=[case[0] for case in BAD_INPUTS])
def test_ui_invariants_alarm_on_bad_input(label, fn, args, expected):
    """**报警能力才是这个工单的全部价值**：坏输入必须报出那个码。"""
    violations = probe(fn, *args)
    assert expected in codes(violations), f"{label}：应当报 {expected}，实际 {codes(violations)}"


# ------------------------------------------------- 自检命令：真渲染那一趟


@needs_render
def test_the_self_check_passes_on_real_rendered_html():
    done = run_node([SELFTEST])
    assert done.returncode == 0, f"界面自检没过：\n{done.stdout}\n{done.stderr}"
    assert "界面自检通过" in done.stdout
    assert "失败 0 条" in done.stdout
    # 每一趟都要把判据对着坏夹具再验一次（验收记录：没失败过的检查同样不可信）
    assert "报警能力 · 题面被换成原图（含手写与订正）→ 判据必须响" in done.stdout


@needs_render
def test_the_self_check_has_no_side_effects():
    """验收 4：**只读检查**不许变成一个会坏的写操作。

    证据是前后两张**真快照**：跑之前拍一张、跑之后拍一张，交给同一份判据比。
    顺带钉住原型的那件具体的事：它跑完会多出一个 `data/index.json`。
    """
    before = snapshot(".")
    done = run_node([SELFTEST])
    after = snapshot(".")
    assert done.returncode == 0, done.stdout + done.stderr

    violations = probe("checkNoSideEffects", {"before": before, "after": after})
    assert violations == [], f"自检动了东西：{violations}"
    assert not (ROOT / "data" / "index.json").exists(), "自检生成了派生索引——原型就是这个病"
    # 快照真的拍到了东西（空快照比出来的"没变"不算数）
    assert len(before["files"]) > 10


@needs_render
def test_the_self_check_needs_no_private_data_directory():
    """它**不读** `data/`——所以 worktree 里天然跑得起来。

    原型相反：没有 `data/vocab` 时直接 `FileNotFoundError`（报的是环境缺文件，
    真因与界面无关）。所以这条断言不是闲聊：它钉住"自检不依赖私人数据"这条性质。
    """
    assert not (ROOT / "data" / "problems").exists() or not list((ROOT / "data" / "problems").glob("*.json"))
    done = run_node([SELFTEST])
    assert done.returncode == 0, done.stdout + done.stderr
    assert "真实数据（`data/` 一眼都没看：夹具是手写的）" in done.stdout


def test_the_self_check_says_so_when_it_could_not_run(tmp_path):
    """环境缺依赖**不是**界面坏了：退出码 2 + 一句「这次没跑完」。

    原型把这两种失败混成一句「界面自检未通过」，于是人被引去修界面。
    """
    lonely = tmp_path / "site"
    shutil.copytree(SITE_TESTS, lonely / "tests")
    shutil.copytree(ROOT / "site" / "src", lonely / "src")
    done = run_node([lonely / "tests" / "selftest.mjs"], cwd=tmp_path)
    assert done.returncode == 2, f"没有渲染依赖时不该报 0/1：\n{done.stdout}\n{done.stderr}"
    assert "这次没跑完（环境）" in done.stdout
    assert "不是**界面不变量失败" in done.stdout
    assert "界面自检通过" not in done.stdout


# --------------------------------- 不变量 4 的反面：会写的自检必须被抓住


def _legacy_env(data_dir: Path) -> dict:
    return {"AI_NOTE_DATA": str(data_dir)}


def test_a_self_check_that_writes_is_caught(tmp_path):
    """照原型形状重写的替身：它先写一份派生索引，然后照样打印"通过"。

    这正是原型被实测复现的那件事（`--selftest` 跑完生成了 `data/index.json`）。
    副作用判据必须抓住它——不然「自检不得有副作用」这条不变量就没有牙齿。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    before = snapshot(data_dir)
    done = run_node([LEGACY], env=_legacy_env(data_dir))
    after = snapshot(data_dir)

    assert done.returncode == 0, "替身自称通过——它自己不会喊"
    assert "界面自检通过" in done.stdout
    violations = probe("checkNoSideEffects", {"before": before, "after": after})
    assert "selftest_created_file" in codes(violations), codes(violations)
    assert any(v["details"]["path"].endswith("index.json") for v in violations)


def test_a_self_check_that_writes_dies_in_a_read_only_environment(tmp_path):
    """只读环境：那个替身报的是**操作系统的错**，不是界面不变量。

    真因（不能写）与界面无关——「把只读检查变成一个会坏的写操作」说的就是这件事。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    data_dir.chmod(0o555)
    try:
        done = run_node([LEGACY], env=_legacy_env(data_dir))
        assert done.returncode != 0
        assert "界面自检通过" not in done.stdout
        assert "EACCES" in (done.stdout + done.stderr) or "permission denied" in (done.stdout + done.stderr).lower()
    finally:
        data_dir.chmod(0o755)


def test_the_self_check_gives_a_true_verdict_where_the_legacy_one_cannot(tmp_path):
    """同一件事的正面：`data/` 根本不存在时，我们的自检照样给出真结论。

    替身在这一步报的是 `FileNotFoundError`（环境缺文件）——原型的老毛病。
    """
    empty = tmp_path / "no-data-here"
    empty.mkdir()
    legacy = run_node([LEGACY], env={"AI_NOTE_DATA": str(empty / "data")})
    assert legacy.returncode != 0
    assert "FileNotFoundError" in (legacy.stdout + legacy.stderr)

    ours = run_node([SELFTEST])
    assert ours.returncode == 0, ours.stdout + ours.stderr
