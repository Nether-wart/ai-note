"""审核那半：待转录清单（`pending`）与"给一张卡跑一次转录"（`transcribe_card`）。

不联网、不花额度：模型调用走注入的 transport，OCR 走注入的假引擎。
"""

from __future__ import annotations

import json

from server import review
from server.catalog import Catalog
from server.ocr import OcrLine, OcrResult
from server.tests.conftest import PNG_1X1, make_data_dir, make_card

PAGE_ID = "abcdef123456"


def cat(root):
    """这些函数收的是 `Catalog`（与仓库里别处一致），不是裸的 Path。"""
    return Catalog(root)


def page_with(card_id, *, image="abcdef123456.png", blocks=None):
    return {
        "id": PAGE_ID,
        "image": image,
        "blocks": blocks if blocks is not None else [
            {"id": "b1", "card_id": card_id, "bbox_norm": [0.1, 0.1, 0.5, 0.2]},
        ],
    }


def write_page(root, page, *, with_image=True):
    (root / "pages").mkdir(parents=True, exist_ok=True)
    (root / "pages" / f"{PAGE_ID}.json").write_text(
        json.dumps(page, ensure_ascii=False), encoding="utf-8")
    if with_image:
        # 真 PNG：`image_data_url` 会**解码**它（要按 MAX_SIDE 降采样），假字节过不去
        (root / "pages" / page["image"]).write_bytes(PNG_1X1)


def card_with_page(pid="p-20200101-aaaaaa", **overrides):
    """**骨架卡**：入库那一刻的形态是转录为空（`page_commit` 就是这么建的）。
    夹具默认的卡带转录，那是"已经审过"的形态——拿它测"待转录"会永远空。"""
    card = make_card(pid, **overrides)
    card["problem"]["transcript"] = ""
    card["problem"]["options"] = []
    card["source"]["page_image"] = f"data/pages/{PAGE_ID}.png"
    return card


def reply(payload):
    def transport(url, headers, payload_in, timeout):
        return 200, json.dumps({"choices": [{"message": {
            "content": json.dumps(payload, ensure_ascii=False)}}]}, ensure_ascii=False)
    return transport


class Config:
    provider, base_url, model, key_env, api_key = "openai", "https://x/v1", "gpt-4o", "K", "sk"


# ------------------------------------------------------------------ 待转录清单


def test_只列还没转录的卡_由新到老(tmp_path):
    old = card_with_page("p-20200101-aaaaaa", created_at="2026-01-01T00:00:00+08:00")
    new = card_with_page("p-20200102-bbbbbb", created_at="2026-02-01T00:00:00+08:00")
    done = make_card("p-20200103-cccccc", created_at="2026-03-01T00:00:00+08:00")
    done["problem"]["transcript"] = "已经转好了"
    root = make_data_dir(tmp_path, [old, new, done])

    rows, broken = review.pending(cat(root))
    assert [row["id"] for row in rows] == ["p-20200102-bbbbbb", "p-20200101-aaaaaa"]
    assert broken == []
    assert rows[0]["page_id"] == PAGE_ID


def test_读不出来的卡要被报出来_不许安静地少一张(tmp_path):
    root = make_data_dir(tmp_path, [])
    (root / "problems" / "p-20200104-dddddd.json").write_text("{ 这不是 JSON", encoding="utf-8")

    rows, broken = review.pending(cat(root))
    assert rows == []
    assert broken and broken[0]["id"] == "p-20200104-dddddd"
    assert "JSON" in broken[0]["reason"]


# ------------------------------------------------------------------ 转录一张


def test_预演不写卡_apply_才写(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]))
    payload = {"transcript": r"6. $\exists x \in [1,4]$ 的一个条件是（ ）",
               "question_no": 6}

    preview = review.transcribe_card(cat(root), card["id"], config=Config(), transport=reply(payload),
                                     runs_dir=tmp_path)
    assert preview["ok"] is True
    assert preview["result"]["transcript"].startswith("6.")
    assert preview["changed"] == [] and preview["applied"] is False
    on_disk = json.loads((root / "problems" / f"{card['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["problem"]["transcript"] == "", "预演不许写盘"

    applied = review.transcribe_card(cat(root), card["id"], config=Config(), transport=reply(payload),
                                     apply=True, runs_dir=tmp_path)
    assert "problem.transcript" in applied["changed"]
    on_disk = json.loads((root / "problems" / f"{card['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["problem"]["transcript"].startswith("6.")
    assert on_disk["provenance"]["model"] == "gpt-4o", "身份要进 provenance"


def test_没有页绑定就明确失败_不去猜一页(tmp_path):
    card = make_card("p-20200101-aaaaaa")
    card["source"].pop("page_image", None)
    root = make_data_dir(tmp_path, [card])
    readout = review.transcribe_card(cat(root), card["id"], config=Config(),
                                     transport=reply({"transcript": "x"}), runs_dir=tmp_path)
    assert readout["ok"] is False
    assert readout["error"]["code"] == review.BINDING_MISSING


def test_页里没有绑这张卡的块也要明确失败(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with("p-另一张卡"))          # 块绑的是别人
    readout = review.transcribe_card(cat(root), card["id"], config=Config(),
                                     transport=reply({"transcript": "x"}), runs_dir=tmp_path)
    assert readout["ok"] is False
    assert readout["error"]["code"] == review.BINDING_MISSING
    assert "没有绑着" in readout["error"]["message"]


def test_照片丢了也要明确失败_页文件在但链断了(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]), with_image=False)
    readout = review.transcribe_card(cat(root), card["id"], config=Config(),
                                     transport=reply({"transcript": "x"}), runs_dir=tmp_path)
    assert readout["ok"] is False
    assert readout["error"]["code"] == "page_image_missing"


def test_问不成时什么都不写_而且可以重试(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]))

    def refusing(url, headers, payload, timeout):
        return 502, "upstream down"

    readout = review.transcribe_card(cat(root), card["id"], config=Config(), transport=refusing,
                                     apply=True, runs_dir=tmp_path)
    assert readout["ok"] is False
    assert readout["error"]["code"] == "model_unavailable"
    on_disk = json.loads((root / "problems" / f"{card['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["problem"]["transcript"] == "", "没问成就什么都不写"


def test_没有_ocr_引擎时读数里写清不可用_草稿长度为零(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]))
    readout = review.transcribe_card(cat(root), card["id"], config=Config(),
                                     transport=reply({"transcript": "题面"}), runs_dir=tmp_path)
    assert readout["ocr_engine"] == "unavailable"
    assert readout["ocr_draft_chars"] == 0


def test_注入_ocr_引擎后草稿进提示词_读数记下是谁(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]))

    def engine(image, name):
        assert isinstance(image, (bytes, bytearray)), "接缝收的是图片字节"
        return OcrResult(lines=[OcrLine(text="草稿这一行", box=[0.15, 0.15, 0.2, 0.05])],
                         engine="paddleocr", model="PP-OCRv6")

    seen = {}

    def transport(url, headers, payload, timeout):
        seen["text"] = json.dumps(payload, ensure_ascii=False)
        return 200, json.dumps({"choices": [{"message": {
            "content": json.dumps({"transcript": "题面"}, ensure_ascii=False)}}]},
            ensure_ascii=False)

    readout = review.transcribe_card(cat(root), card["id"], config=Config(), ocr=engine,
                                     transport=transport, runs_dir=tmp_path)
    assert readout["ok"] is True
    assert readout["ocr_engine"] == "paddleocr"
    assert readout["ocr_draft_chars"] == len("草稿这一行")
    assert "草稿这一行" in seen["text"], "草稿要真的进提示词"
    assert "以图为准" in seen["text"]


def test_解析不出来时调用算成功_但一个字段都不写(tmp_path):
    card = card_with_page()
    root = make_data_dir(tmp_path, [card])
    write_page(root, page_with(card["id"]))

    def transport(url, headers, payload, timeout):
        return 200, json.dumps({"choices": [{"message": {"content": "我看不清这道题"}}]},
                               ensure_ascii=False)

    readout = review.transcribe_card(cat(root), card["id"], config=Config(), transport=transport,
                                     apply=True, runs_dir=tmp_path)
    assert readout["ok"] is True, "调用是成功的，只是没读出来"
    assert readout["result"]["parsed"] is False
    # **内容**一个字段都不写；但**"问过这件事"要留痕**——provenance 记下是谁问的、哪一次。
    # 不留痕的话，下一次跑起来跟没问过一模一样，而"没问成"与"没问过"是两件事。
    content = [name for name in readout["changed"] if not name.startswith("provenance.")]
    assert content == [], f"没读出来就不许写内容：{content}"
    assert set(readout["changed"]) <= {"provenance.provider", "provenance.model",
                                       "provenance.run_id"}
    on_disk = json.loads((root / "problems" / f"{card['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["problem"]["transcript"] == ""
    assert "我看不清这道题" in readout["result"]["reason"], "模型原话要留下"


def test_清单为空时_CLI_说清没有待转录的(tmp_path, capsys):
    root = make_data_dir(tmp_path, [])
    assert review.main(["list", "--data", str(root)]) == 0
    assert "没有待转录的卡" in capsys.readouterr().out
