"""页：一等实体（spec #2、CONTEXT「页」、契约 §10.2、编排裁决 D5）。

测的是**行为**：一张存量卡进去，一个页文件出来；一份新旧块列表进去，
一条「谁保留、谁新增、谁消失」的对照出来。不测内部函数名、不联网、不画图。
"""

from __future__ import annotations

import json

from conftest import make_card
from server import pages

# 真实存量数据（只读核对过）：一页一题，页照片 12 位哈希。
REAL_PAGE_HASH = "41c86bcfc007"
REAL_PID = "p-20261004-41c86b"


def real_shaped_card(pid: str = REAL_PID, page_hash: str = REAL_PAGE_HASH) -> dict:
    """一张与 `data/problems/p-20261004-41c86b.json` 同形状的存量卡。

    回填所需的料全在 `source`：整页照片 + 单块边界（`bbox_norm`；`bbox_px` 读盘上原值）。
    """
    return make_card(
        pid,
        **{
            "created_at": "2026-10-04T14:31:35+08:00",
            "source.page_image": f"data/pages/{page_hash}.png",
            "source.bbox_norm": [0.02, 0.12, 0.76, 0.28],
            "source.bbox_px": [3, 20, 541, 79],
            "source.original_file": "2.png",
        },
    )


def read_page(api, page_id: str = REAL_PAGE_HASH) -> dict:
    path = api.catalog.pages_dir / f"{page_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_page_id_comes_from_the_photo_filename_not_the_card_id():
    """真实数据的坑：页照片是 12 位哈希、题卡 id 是 6 位——长度都不一样（D5）。

    `data/problems/p-20261004-41c86b.json` → `data/pages/41c86bcfc007.png`
    所以页文件叫 `41c86bcfc007.json`，**不是** `41c86b.json`。
    """
    assert pages.page_hash_from_image("data/pages/41c86bcfc007.png") == "41c86bcfc007"
    assert pages.page_hash_from_image("41c86bcfc007.png") == "41c86bcfc007"
    assert pages.page_hash_from_image("/abs/data/pages/ef7c47156392.jpeg") == "ef7c47156392"


def test_page_id_is_none_when_there_is_no_usable_photo_name():
    """没有照片就没有页——不猜、不从题卡 id 兜底。"""
    assert pages.page_hash_from_image(None) is None
    assert pages.page_hash_from_image("") is None
    assert pages.page_hash_from_image("data/pages/") is None


def test_page_id_is_none_for_a_name_that_is_not_a_single_safe_component():
    """页 id 会变成文件名，所以它必须是一个安全的文件名片段（纵深防御，§7.3 同款）。"""
    assert pages.page_hash_from_image("data/pages/../../etc/passwd") is None
    assert pages.page_hash_from_image("data/pages/..png") is None


# ---------------------------------------------------------------- 验收 1：存量回填


def test_backfill_turns_a_legacy_card_into_a_page_file(api_for):
    """验收 1：存量卡反推成页文件——照片 + 单个块 = 那张卡的边界。

    真实数据里两张卡各是「一页一题」，所以回填出两个页文件、各一个块。
    """
    api = api_for([real_shaped_card()])

    report = pages.backfill_pages(api.catalog, apply=True)

    assert report["apply"] is True
    assert [c["action"] for c in report["cards"]] == ["created"]
    assert report["summary"]["created"] == 1

    page = read_page(api)
    assert page["version"] == 1
    assert page["id"] == REAL_PAGE_HASH, "页 id 是照片文件名主干，不是题卡 id 的 6 位"
    assert page["image"] == f"{REAL_PAGE_HASH}.png", "页文件与照片同目录并列（D5）"
    assert page["created_at"] == "2026-10-04T14:31:35+08:00"
    assert page["origin"] == {
        "original_file": "2.png",
        "sheet": None,        # 来源信息：第几页、哪张卷子（spec #2），存量数据没有
        "page_number": None,
    }

    (block,) = page["blocks"]
    assert block["bbox_norm"] == [0.02, 0.12, 0.76, 0.28], "规范基准是整页归一化坐标"
    assert block["bbox_px"] == [3, 20, 541, 79], "读盘上的原值，不用 bbox_norm 重算"
    assert block["card_id"] == REAL_PID
    assert block["keep"] is True, "卡片在库里就是在「收」——存量卡的去留不是待定"
