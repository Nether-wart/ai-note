"""运行时的文件放哪：**用户数据目录，不是项目目录**。

理由：项目目录是代码与证据——它随时可以被 clone、清空、重新检出。而这里放的是
**你的资料**（题卡、照片、页文件）与模型调用留档：它们不该跟代码同生共死，也不该
出现在项目根的 `git status` 里。仓库里的 `data/` 从此只做**测试语料**。

平台惯例（`server/` 零第三方依赖，所以按环境变量自己推，不引 `platformdirs`）：

| 平台 | 位置 |
|---|---|
| Windows | `%APPDATA%\\ai-note`（Roaming） |
| macOS | `~/Library/Application Support/ai-note` |
| 其它（Linux 等） | `$XDG_DATA_HOME/ai-note`，没设就是 `~/.local/share/ai-note` |

优先级：命令行 `--data` > `AI_NOTE_DATA` > 上面那张表的默认。
模型调用留档在同一目录下的 `runs/`（`AI_NOTE_RUNS` 可单独覆盖）。

**默认值一律在用到它的那一刻算**（建 parser 时、构造 `Api` 时），不在 import 时算：
`.env.local` 是在 `main()` 里、建 parser **之前**才灌进环境的，import 时算就等于让
写在 `.env.local` 里的 `AI_NOTE_*` 静默失效（ADR 0007 第 6 条：不许静默）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_DIR_NAME = "ai-note"


def user_data_dir(env: Mapping[str, str] | None = None, *, platform: str | None = None,
                  home: str | Path | None = None) -> Path:
    """本应用的用户数据目录。参数都是**测试接缝**：不传就读真环境。

    先看平台自己的变量（Windows 的 `%APPDATA%`、POSIX 的 `$XDG_DATA_HOME`），
    没有就退回该平台的惯例路径。
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)

    if platform.startswith("win"):
        base = env.get("APPDATA") or env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_DIR_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_DIR_NAME


def _env_path(env: Mapping[str, str], name: str) -> Path | None:
    """环境变量给的一个路径；空串与只有空白等于没给。"""
    raw = (env.get(name) or "").strip()
    return Path(raw).expanduser() if raw else None


def default_data_dir(env: Mapping[str, str] | None = None, **kwargs) -> Path:
    """数据目录：`AI_NOTE_DATA` 优先，否则用户数据目录。"""
    env = os.environ if env is None else env
    return _env_path(env, "AI_NOTE_DATA") or user_data_dir(env, **kwargs)


def default_runs_dir(env: Mapping[str, str] | None = None, **kwargs) -> Path:
    """模型调用留档目录：`AI_NOTE_RUNS` 优先，否则 `<数据目录>/runs`。"""
    env = os.environ if env is None else env
    return _env_path(env, "AI_NOTE_RUNS") or default_data_dir(env, **kwargs) / "runs"
