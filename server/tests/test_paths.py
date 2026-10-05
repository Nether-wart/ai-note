"""运行时的文件放哪（`server/paths.py`）。

这份测试钉住两件事：

1. **默认位置不在仓库里**——仓库的 `data/` 只做测试语料，生产数据与留档放用户数据目录；
2. 平台与优先级矩阵：平台惯例、`$XDG_DATA_HOME` / `%APPDATA%`、以及
   `AI_NOTE_DATA` / `AI_NOTE_RUNS` 的覆盖关系。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import paths

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform,env,want", [
    # Linux / 其它 POSIX：XDG 规范
    ("linux", {}, Path("/home/me/.local/share/ai-note")),
    ("linux", {"XDG_DATA_HOME": "/xdg/data"}, Path("/xdg/data/ai-note")),
    # macOS：Application Support（XDG 变量在 macOS 上不管用）
    ("darwin", {}, Path("/home/me/Library/Application Support/ai-note")),
    ("darwin", {"XDG_DATA_HOME": "/xdg"}, Path("/home/me/Library/Application Support/ai-note")),
    # Windows：Roaming 优先，其次 Local，都没有才退回惯例路径
    ("win32", {"APPDATA": r"C:\Users\me\AppData\Roaming"},
     Path(r"C:\Users\me\AppData\Roaming") / "ai-note"),
    ("win32", {"LOCALAPPDATA": r"C:\Users\me\AppData\Local"},
     Path(r"C:\Users\me\AppData\Local") / "ai-note"),
    ("win32", {}, Path("/home/me/AppData/Roaming/ai-note")),
])
def test_user_data_dir_matrix(platform, env, want):
    assert paths.user_data_dir(env, platform=platform, home="/home/me") == want


def test_data_dir_prefers_the_env_var_and_ignores_a_blank_one():
    assert paths.default_data_dir({"AI_NOTE_DATA": "/tmp/elsewhere"}) == Path("/tmp/elsewhere")
    assert paths.default_data_dir({"AI_NOTE_DATA": "   ", "XDG_DATA_HOME": "/xdg"},
                                  platform="linux", home="/home/me") == Path("/xdg/ai-note")


def test_runs_live_under_the_data_dir_and_can_be_overridden():
    assert paths.default_runs_dir({"AI_NOTE_DATA": "/tmp/d"}) == Path("/tmp/d/runs")
    assert paths.default_runs_dir({"AI_NOTE_DATA": "/tmp/d", "AI_NOTE_RUNS": "/tmp/r"}) \
        == Path("/tmp/r")


def test_env_path_expands_a_leading_tilde():
    assert paths.default_data_dir({"AI_NOTE_DATA": "~/错题本"}) == Path.home() / "错题本"


def test_the_defaults_never_live_in_the_repository():
    """这条就是这次改动的理由：默认数据目录与留档目录都不许落在项目目录里。"""
    kwargs = {"platform": "linux", "home": "/home/me"}
    for p in (paths.default_data_dir({}, **kwargs), paths.default_runs_dir({}, **kwargs)):
        assert ROOT not in p.parents and ROOT != p, f"{p} 落在仓库里了"


def test_parser_defaults_are_computed_when_the_parser_is_built(monkeypatch):
    """`.env.local` 是在建 parser **之前**才灌进环境的，所以默认值必须**那时**才算。

    这条防的是「import 时算好默认值」——那会让写在 `.env.local` 里的 `AI_NOTE_*` 静默失效
    （ADR 0007 第 6 条；这个项目在 `.env.local` 上栽过一次，正是同一类失败）。
    """
    from server import app, audit, intake

    monkeypatch.setenv("AI_NOTE_DATA", "/tmp/from-env-data")
    monkeypatch.setenv("AI_NOTE_RUNS", "/tmp/from-env-runs")
    assert app.build_parser().parse_args([]).data == "/tmp/from-env-data"
    assert audit.build_parser().parse_args([]).data == "/tmp/from-env-data"
    assert intake.build_parser().parse_args(["--page", "x"]).runs_dir == "/tmp/from-env-runs"


def test_http_layer_falls_back_to_the_env_runs_dir(monkeypatch, tmp_path):
    from server.http import Api

    monkeypatch.setenv("AI_NOTE_RUNS", str(tmp_path / "runs"))
    api = Api(tmp_path)
    assert api.attempts.judge_call.runs_dir == tmp_path / "runs"
