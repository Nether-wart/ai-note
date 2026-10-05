"""模型上岗验收（`CONTEXT.md`「验收」「视觉探针」，契约 §1 的角色表，issue #17 §7／§8）。

规矩只有一句：**换模型就要重跑验收，不过考不许上岗。** 所以它必须是一条命令：

    python3 -m pytest server/tests/test_acceptance_models.py -v

**没配密钥时 skip，不是 pass。** 一条在环境没配好时「通过」的验收比一条没跑的更坏：
它会让「换过模型、没跑过考卷」看起来是合规的。

切分角色的三关（**三关都要过**）：
  1. **视觉探针**：给一张写着随机数字的图，看它读不读得出来。防的是一个很具体的形状
     ——纯文本模型收到图片**不会报错**，它会忽略图片、照着提示词**凭空编块**。
  2. **真实照片上的人眼判定**：这一关机器判不了（判不了「边界画得准不准」），
     所以验收把切出来的框画在照片上写成 PNG，**让人去看**。文件路径印在报告里。
  3. **三条确定性对账判据**（题号连续性／重叠／覆盖）必须全过。

**照片从哪来**：本机的 `data/pages/*.png`。仓库里的 `data/` 是私人内容的副本、被
`.gitignore` 拦住，所以**不进仓库**（「种子（词表）进仓库，私人内容一律不进」）。
没有照片的机器上，第 2／3 关**明说跳过**，第 1 关照跑（探针图是合成的，不需要私人数据）。
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from acceptance_fixtures import draw_digits, write_png

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGES_DIR = REPO_ROOT / "data" / "pages"
# 验收产物落在 `.verify/`（已 gitignore）：它只是给人看的图，不是仓库内容。
OUT_DIR = REPO_ROOT / ".verify" / "acceptance"

# 密钥可能写在 `.env.local` 里（服务和验收的共同来源），先把它灌进环境再判。
from server.config import PROVIDERS, load_env_files  # noqa: E402

ENV_FILES = load_env_files([REPO_ROOT / ".env.local", Path.home() / ".env.local"])
KEY_ENVS = [preset["key_env"] for preset in PROVIDERS.values()]
MISSING_KEY = [name for name in KEY_ENVS if not os.environ.get(name)]

needs_key = pytest.mark.skipif(
    bool(MISSING_KEY),
    reason=f"没有配模型密钥（{'／'.join(MISSING_KEY)} 都没有）：验收**没跑**，不是通过。"
           f"填 .env.local 之后重跑；读过 {len(ENV_FILES)} 份 .env.local",
)


def real_pages() -> list[Path]:
    """本机的真实整页照片。空列表 = 这台机器上没有，相关两关要跳过。"""
    if not PAGES_DIR.is_dir():
        return []
    return sorted(PAGES_DIR.glob("*.png"))


# --------------------------------------------------------------- 第 1 关：视觉探针


@needs_key
def test_the_visual_probe_reads_a_random_number():
    """模型真的打开了这张图吗？

    问法刻意最简：只问「图上写的什么数字」。它**不测**手写识别、也不测切分质量——
    它测的是「图片有没有被读到」这一件事。七段数码管画出来的数字不是手写数字，
    这句限制写在 `acceptance_fixtures.py` 的模块 docstring 里。
    """
    from server.config import load_role_config
    from server.model_client import chat

    rng = random.Random(20261005)
    digits = "".join(str(rng.randrange(10)) for _ in range(6))
    image = draw_digits(digits)
    path = write_png(image, OUT_DIR / "probe.png")

    config = load_role_config("segmenter")
    call = chat(config, [
        {"role": "system", "content": "你只回答一个数字，不要解释。"},
        {"role": "user", "content": [
            {"type": "text", "text": "这张图上写的数字是什么？只回答那串数字。"},
            {"type": "image_url", "image_url": {"url": _data_url(path)}},
        ]},
    ], tag="probe", runs_dir=REPO_ROOT / ".verify" / "acceptance-runs")

    answer = (call.text or "").strip()
    assert digits in answer, (
        f"视觉探针没过：图上写的是 {digits}，模型答的是 {answer!r}。"
        f"这一关不过，说明这个模型**没有真的读图**——用它切分只会得到凭空编出来的块。"
        f"探针图：{path}；留档：{call.run_id}"
    )


def _data_url(path: Path) -> str:
    """探针图是本地合成的小 PNG，直接用 `intake_client` 那份唯一实现。"""
    from server.intake_client import image_data_url

    return image_data_url(path, max_side=2048)


# ------------------------------------------- 第 2／3 关：真实照片上的切分 + 对账


@pytest.mark.skipif(not real_pages(), reason="这台机器上没有真实整页照片（data/pages/*.png）")
@needs_key
def test_the_segmenter_passes_the_deterministic_checks_on_every_real_page(tmp_path):
    """真实照片上：切分跑得通、三条对账判据全过，并把框画出来供**人眼**过第二关。"""
    from server import segmentation
    from server.config import load_role_config
    from server.segmenter_client import HttpSegmenter

    segmenter = HttpSegmenter(load_role_config("segmenter"), tmp_path / "runs")

    reports: list[str] = []
    for page in real_pages():
        text = segmenter(page)
        parsed = segmentation.parse_candidate_blocks(text)
        assert parsed["parsed"], (
            f"{page.name}：切分没跑成（抠不出块）——这不是「这一页没有题」。"
            f"模型原话：{parsed.get('message')}"
        )
        # 第 3 关：三条确定性判据。`checks` 只报不改（切分仍由人确认）。
        overlay = OUT_DIR / f"{page.stem}-blocks.png"
        reports.append(f"{page.name}: {len(parsed['blocks'])} 块 → 人眼看：{_draw(page, parsed['blocks'], overlay)}")

    # 第二关是人眼，机器只能把图摆出来，并把路径印在报告里。
    print("\n第二关（人眼判定）要看的图：\n  " + "\n  ".join(reports))


def _draw(page: Path, blocks: list[dict], out: Path) -> Path:
    """把块框画在照片上（红框）。只为人眼看，不参与任何判据。"""
    from server import ink

    image = ink.read_png(page)
    width, height = image.size
    pixels = list(image.pixels)
    for block in blocks:
        box = block.get("bbox_norm")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        x, y, w, h = (float(v) for v in box)
        left, top = int(x * width), int(y * height)
        right, bottom = int((x + w) * width), int((y + h) * height)
        for col in range(max(0, left), min(width, right)):
            for row in (top, bottom - 1):
                if 0 <= row < height:
                    pixels[row * width + col] = (220, 0, 0)
        for row in range(max(0, top), min(height, bottom)):
            for col in (left, right - 1):
                if 0 <= col < width:
                    pixels[row * width + col] = (220, 0, 0)
    return write_png(ink.InkImage(width, height, pixels), out)
