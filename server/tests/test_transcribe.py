"""转录这一步（`server/transcribe.py`）：提问、解析、草稿切片、写进卡。

不联网：`transcribe()` 走注入的 transport，其余都是纯逻辑。
"""

from __future__ import annotations

import json

from server import transcribe
from server.ocr import OcrLine, OcrResult, unavailable


BLOCK = {"id": "b1", "bbox_norm": [0.1, 0.2, 0.5, 0.1]}


def reply(payload, *, model="gpt-4o", provider="openai"):
    def transport(url, headers, payload_in, timeout):
        assert url.endswith("/chat/completions")
        return 200, json.dumps({
            "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {},
        }, ensure_ascii=False)
    return transport


# ------------------------------------------------------------------ 提问


def test_没有_ocr_时提示词里一个字都不提它():
    messages = transcribe.transcript_messages(block=BLOCK, image_url="data:image/png;base64,x")
    text = messages[1]["content"][0]["text"]
    assert "OCR" not in text, "没注入引擎时不许出现草稿那一段"
    assert "整页" in text and "0.100" in text, "位置照旧要说清"


def test_有_ocr_草稿时明确写清可能有错要以图为准():
    messages = transcribe.transcript_messages(block=BLOCK, image_url="x",
                                              ocr_text="6. 设 $x^2+ax+4$ 的一个条件是（ ）")
    text = messages[1]["content"][0]["text"]
    assert "本地 OCR 草稿" in text
    assert "以图为准" in text
    assert "x^2+ax+4" in text


def test_系统提示里那几条铁律要在_尤其不许编():
    system = transcribe.TRANSCRIPT_SYSTEM
    assert "图是准绳" in system
    assert "不许编" in system
    assert "LaTeX" in system
    assert "unreadable" in system


# ------------------------------------------------------------------ 草稿切片


def lines(*items):
    return OcrResult(lines=[OcrLine(text=text, box=box) for text, box in items],
                     engine="paddleocr", model="PP-OCRv6")


def test_只取落在这一块里的行():
    result = lines(("这道题", [0.1, 0.2, 0.2, 0.05]),      # 中心 (0.2, 0.225) → 块内
                   ("另一段的字", [0.1, 0.8, 0.2, 0.05]))   # 中心 (0.2, 0.825) → 块外
    draft = transcribe.draft_for_block(result, BLOCK["bbox_norm"])
    assert draft == "这道题"


def test_只要有一行没有框_就不再筛_宁可多给也不悄悄丢():
    result = lines(("块内", [0.1, 0.2, 0.1, 0.05]), ("没有框的一行", None))
    draft = transcribe.draft_for_block(result, BLOCK["bbox_norm"])
    assert "块内" in draft and "没有框的一行" in draft


def test_没有引擎或没有行时草稿是空串_调用方据此一个字都不给():
    assert transcribe.draft_for_block(None, BLOCK["bbox_norm"]) == ""
    assert transcribe.draft_for_block(unavailable("没装"), BLOCK["bbox_norm"]) == ""
    assert transcribe.draft_for_block(OcrResult(lines=[], engine="paddleocr"),
                                      BLOCK["bbox_norm"]) == ""


def test_块边界读不出来时给整页草稿_并说清它是整页的():
    result = lines(("任意一行", [0.9, 0.9, 0.01, 0.01]))
    assert transcribe.draft_for_block(result, None) == "任意一行"
    messages = transcribe.transcript_messages(block={}, image_url="x", ocr_text="任意一行")
    assert "整页" in messages[1]["content"][0]["text"]


# ------------------------------------------------------------------ 解析


def test_解析好坏两种都要把原话留下():
    good = transcribe.parse_transcript(json.dumps({
        "transcript": r"6. $\exists x \in [1,4]$ 的一个充分不必要条件是（ ）",
        "options": [{"label": "A", "text": "x=1"}, {"label": "B"}],
        "question_no": 6, "standard_answer": {"value": "B", "confidence": 0.8},
        "topics": ["函数与导数/极值与最值"], "error_causes": ["概念不清"],
        "unreadable": ["第 2 行末尾的符号"], "notes": "草稿把 6 读成了 5", "confidence": 0.7,
    }, ensure_ascii=False))
    assert good["parsed"] is True
    assert good["question_no"] == 6
    assert good["options"][1] == {"label": "B", "text": ""}
    assert good["standard_answer"] == {"value": "B", "confidence": 0.8}
    assert good["notes"] == "草稿把 6 读成了 5"

    bad = transcribe.parse_transcript("我看不清这道题")
    assert bad["parsed"] is False
    assert bad["transcript"] is None
    assert "我看不清这道题" in bad["reason"], "原话要留下，不许只说解析失败"


def test_假数字与坏类型一律记_none_或空_不记假值():
    result = transcribe.parse_transcript(json.dumps({
        "transcript": "题面", "confidence": 1.5, "question_no": True,
        "standard_answer": {"value": "", "confidence": "高"}, "options": "A,B",
        "topics": [1, "  ", "有效考点"],
    }, ensure_ascii=False))
    assert result["confidence"] is None
    assert result["question_no"] is None
    assert result["standard_answer"] is None
    assert result["options"] == []
    assert result["topics"] == ["有效考点"]


# ------------------------------------------------------------------ 写进卡


def skeleton():
    return {"problem": {"transcript": "", "options": []},
            "standard_answer": {"value": None, "confidence": None},
            "topics": [], "error_causes": [], "provenance": {}}


def test_只填读出来的_读不出来的一律不动():
    card = skeleton()
    card["standard_answer"] = {"value": "人后来补的答案", "confidence": 0.9}
    changed = transcribe.apply_to_card(card, {
        "transcript": "题面", "options": [], "standard_answer": None,
        "topics": [], "error_causes": [], "unreadable": [], "notes": None,
        "provider": "openai", "model": "gpt-4o", "run_id": "r1",
    })
    assert card["problem"]["transcript"] == "题面"
    assert card["standard_answer"]["value"] == "人后来补的答案", "没读到不等于清空"
    assert changed == ["problem.transcript", "provenance.provider",
                       "provenance.model", "provenance.run_id"]


def test_看不清的地方要落进_provenance_而不是被丢掉():
    card = skeleton()
    changed = transcribe.apply_to_card(card, {
        "transcript": "题面", "unreadable": ["第 2 行末尾的符号"], "notes": "草稿与图不一致",
    })
    assert card["provenance"]["unreadable"] == ["第 2 行末尾的符号"]
    assert card["provenance"]["model_notes"] == "草稿与图不一致"
    assert "provenance.unreadable" in changed


def test_transcribe_把身份贴进答案_且失败时抛出去():
    from server.model_client import ModelUnavailable

    def transport(url, headers, payload, timeout):
        return 200, json.dumps({"choices": [{"message": {"content": json.dumps(
            {"transcript": "题面"}, ensure_ascii=False)}}]}, ensure_ascii=False)

    class Config:
        provider, base_url, model, key_env, api_key = "openai", "https://x/v1", "gpt-4o", "K", "sk"

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        answer = transcribe.transcribe(block=BLOCK, image_url="x", config=Config(),
                                       runs_dir=tmp, transport=transport, env={"K": "sk"})
    assert answer["transcript"] == "题面"
    assert answer["provider"] == "openai" and answer["model"] == "gpt-4o"

    def refusing(url, headers, payload, timeout):
        return 500, "boom"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            transcribe.transcribe(block=BLOCK, image_url="x", config=Config(),
                                  runs_dir=tmp, transport=refusing, env={"K": "sk"})
    except ModelUnavailable:
        pass
    else:
        raise AssertionError("调用失败要抛 ModelUnavailable，让调用方报 502")
