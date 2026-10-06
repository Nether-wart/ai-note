"""本地 OCR 那条路的接缝（`server/ocr.py`）与闸门（`server/ocr_gate.py`）。

不注入引擎 ＝ **不可用**（照抄 `segmenter=None` 的语义）。判据都不依赖任何真引擎：
PaddleOCR 装没装、跑不跑得动，与这一层无关。
"""

from __future__ import annotations

from server import ocr, ocr_gate


def fake_engine(lines, *, engine="paddleocr", model="PP-OCRv6"):
    def run(image, name):
        assert isinstance(image, (bytes, bytearray)), "接缝收的是图片字节"
        return ocr.OcrResult(lines=[ocr.OcrLine(text=text) for text in lines],
                             engine=engine, model=model)
    return run


# ------------------------------------------------------------------ 接缝


def test_不注入引擎就是不可用_而不是空结果():
    result = ocr.read(None, b"png")
    assert result.available is False
    assert result.engine == ocr.UNAVAILABLE_ENGINE
    assert result.lines == []
    assert result.warnings and "没有注入" in result.warnings[0]
    assert result.text() == ""


def test_引擎给的读数会被原样带出去():
    result = ocr.read(fake_engine(["第一行", "第二行"]), b"png", name="clean.png")
    assert result.available is True
    assert result.engine == "paddleocr" and result.model == "PP-OCRv6"
    assert result.text() == "第一行\n第二行", "给'整结构'那一步的是一段文本"


def test_空行不算行_但不假装没有行():
    result = ocr.read(fake_engine(["", "  ", "正文"]), b"png")
    assert result.text() == "正文"
    assert len(result.lines) == 3, "空行也要留在读数里（它是引擎给的原文）"


def test_引擎返回别的东西是接线错误_不是没识别出来():
    def wrong(image, name):
        return "就是一行文本"
    try:
        ocr.read(wrong, b"png")
    except TypeError as exc:
        assert "OcrResult" in str(exc)
    else:
        raise AssertionError("返回类型不对必须喊出来，不能当成空结果")


def test_置信度允许缺席_缺席不等于零():
    line = ocr.OcrLine(text="x")
    assert line.confidence is None
    assert ocr.OcrLine(text="x", confidence=0.0).confidence == 0.0


# ------------------------------------------------------------------ 闸门


def test_好修补放行_字符级噪声被修好就属于这一类():
    # `∃`/`∈` 是原文里就有的，模型只是把它们摆进 LaTeX 结构
    verdict = ocr_gate.check("∃x ∈ [1,4]", r"$\exists x \in [1,4]$")
    assert verdict["ok"] is True, verdict


def test_凭空多出一个符号就退回():
    # 原文没有 `≤`，修补版有 → 那是编的
    verdict = ocr_gate.check("x < 3", r"x \le 3")
    assert verdict["ok"] is False
    assert verdict["code"] == "symbol_invented"
    assert "≤" in verdict["detail"]["symbols"] or "\\" in verdict["detail"]["symbols"]


def test_数字变了或丢了就退回_这是最坏的一种():
    assert ocr_gate.check("答 1 2", "答 12")["code"] == "number_changed"
    assert ocr_gate.check("2026 年", "2025 年")["code"] == "number_changed"
    verdict = ocr_gate.check("x = 12", "x = 1")
    assert verdict["code"] == "number_changed"
    assert verdict["detail"]["lost"] == ["12"]


def test_改得太多就算重写而不是修补():
    verdict = ocr_gate.check("求函数的最小值", "求函数在给定区间上的最小值与最大值")
    assert verdict["ok"] is False
    assert verdict["code"] == "too_far_rewritten"
    assert verdict["detail"]["ratio"] > verdict["detail"]["limit"]


def test_原文是空的就退回_没得修():
    assert ocr_gate.check("", "随便")["code"] == "empty_source"
    assert ocr_gate.check("   ", "随便")["code"] == "empty_source"


def test_换行与空格怎么变都不算重写():
    verdict = ocr_gate.check("第一行 第二行", "第一行\n第二行")
    assert verdict["ok"] is True, verdict


def test_编辑距离是标准的_莱文斯坦():
    assert ocr_gate.edit_distance("", "") == 0
    assert ocr_gate.edit_distance("abc", "") == 3
    assert ocr_gate.edit_distance("kitten", "sitting") == 3
    assert ocr_gate.edit_distance("同", "同") == 0


def test_闸门返回结构化结论_不打日志不抛异常():
    verdict = ocr_gate.check("x < 3", "y > 4")
    assert set(verdict) == {"ok", "code", "detail"}
    assert isinstance(verdict["detail"], dict)
