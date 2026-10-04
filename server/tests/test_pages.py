"""页：一等实体（spec #2、CONTEXT「页」、契约 §10.2、编排裁决 D5）。

测的是**行为**：一张存量卡进去，一个页文件出来；一份新旧块列表进去，
一条「谁保留、谁新增、谁消失」的对照出来。不测内部函数名、不联网、不画图。
"""

from __future__ import annotations

from server import pages


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
