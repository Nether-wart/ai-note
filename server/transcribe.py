"""把一页照片里的**一道题**录成结构化记录（契约里那张骨架卡缺的就是这一步）。

背景（这一笔要解决的问题）：`page_commit` 建出来的卡是**骨架**——`problem.transcript` 是空串，
而**全仓库没有任何一步在写它**（`warnings.py` 检查它空不空、`records.py` 读它，仅此而已）。
所以"审核"要做的第一件事是"转录"，而它一直缺着。

三件与既有模块**刻意保持一致**的事：

1. **输入是整页、擦除手写后的那张图**（`images.clean`）＋ 这一块的边界。不是裁剪图——
   裁剪要另存文件，而"不记一条取不到的文件路径"是这条管道已有的纪律。
2. **提示词与解析分工**：这一层只管"怎么问"与"怎么读回执"，**不做语义判断**
   （与 `intake_client` 同一形状：`parse_*` 从不抛异常，抠不出 JSON 就把模型原话留下）。
3. **OCR 只是草稿**（`ocr_text`）。图是准绳；草稿与图冲突时以图为准，并要求模型在 `notes` 里写清。
   OCR 引擎不注入时**一个字节都不给**（提示词与没有 OCR 时逐字相同），
   这样"有没有 OCR"是两件可分辨的事，而不是悄悄混在一起。
"""

from __future__ import annotations

from .model_client import chat
from .ocr import OcrResult

#: 留档用的 tag（与 `intake_client.INTAKE_TAG` 分开：转录是另一件事，留档要分得开）。
TRANSCRIBE_TAG = "transcribe"

#: 草稿最多给这么多字符——整页草稿可能很长，而模型要的是"这一块"的那几行。
MAX_DRAFT_CHARS = 4000

TRANSCRIPT_SYSTEM = """你在把一页试卷照片里的**一道题**录成结构化的记录。你会收到：整页照片（**已擦除手写**的那一版）、这一块的边界、以及（有时）一份本地 OCR 草稿。

只输出一个 JSON 对象，不要解释、不要 markdown 代码块。字段：

- `transcript`：题面原文。**逐字照抄**，不要改写、不要补全、不要翻译。数学一律 LaTeX（`$...$`）。
- `options`：`[{"label": "A", "text": "..."}]`；没有选项就给 `[]`。
- `question_no`：题号（整数或字符串）；看不清给 `null`。
- `standard_answer`：**印刷体里就印着答案**（如"参考答案"）时给 `{"value": "...", "confidence": 0.0~1.0}`；没有就给 `null`。
- `topics`：考点，每条形如 `"章/节"` 或 `"章/节/点"`；不确定就给 `[]`。
- `error_causes`：错因；不确定就给 `[]`。
- `unreadable`：你**看不清**的部分，逐条写清是哪里（例如 `"第 2 行末尾的符号"`）。
- `notes`：一句中文，只在有该说的（例如草稿与图不一致）时写。
- `confidence`：`0.0~1.0` 或 `null`。

铁律：

1. **图是准绳**。OCR 草稿只是提示，可能读错；与图冲突时以图为准，并把冲突写进 `notes`。
2. **不许编**。看不清就留空并写进 `unreadable`。**编一个看起来合理的公式，比留空坏得多**——
   留空会被 `problem_transcript_missing` 抓到，编错了谁也发现不了。
3. **手写已经擦掉**，你读的是印刷体。若图里仍有手写残留，不要把它当题面。
4. `topics` 与 `error_causes` 宁少勿错：它们会进索引与统计，编出来的考点会污染大纲。"""


def draft_for_block(result: OcrResult | None, bbox_norm, *, limit: int = MAX_DRAFT_CHARS) -> str:
    """整页 OCR 草稿 → **这一块**的那几行。

    有框的行按框中心落在块内来筛（`bbox_norm` 是归一化 xywh，与页坐标同一套口径）。
    **只要有一行没有框**，就不再筛——那种情况下"筛"会悄悄丢掉内容，
    宁可把整页草稿交出去并在提示里说清它是整页的。

    返回空串表示"没有可用的草稿"，调用方据此**一个字都不给**（而不是给一段空标题）。
    """
    if result is None or not result.available or not result.lines:
        return ""

    box = None
    if isinstance(bbox_norm, (list, tuple)) and len(bbox_norm) == 4:
        try:
            x, y, w, h = (float(value) for value in bbox_norm)
            box = (x, y, x + w, y + h)
        except (TypeError, ValueError):
            box = None

    lines = list(result.lines)
    if box is not None and all(line.box for line in lines):
        kept = []
        for line in lines:
            lx, ly, lw, lh = (float(value) for value in line.box[:4])
            cx, cy = lx + lw / 2, ly + lh / 2
            if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
                kept.append(line.text)
        lines_text = kept
    else:
        lines_text = [line.text for line in lines]

    text = "\n".join(part for part in lines_text if part.strip())
    return text[:limit]


#: 这张图是**哪一种**。提示词必须说实话——说错了模型会照着一个不存在的承诺去读。
IMAGE_KINDS = {
    "page": "图片是**整页**试卷照片（**手写可能还在**：只读印刷体的题面，忽略手写与红笔）。",
    "clean": "图片是整页试卷照片的**擦除手写版**（应该只剩印刷体；若仍有手写残留，不要当题面）。",
}


def transcript_messages(*, block: dict, image_url: str, ocr_text: str = "",
                        image_kind: str = "page") -> list[dict]:
    """`[system, user]` 两条消息。`ocr_text` 为空时**与没有 OCR 时逐字相同**。

    位置的说辞与 `intake_client.semantics_messages` 同源（同一个块、同一套坐标），
    但**问的不是同一件事**：这里问"题面是什么"，那里问"红笔是什么意思"。

    `image_kind` 决定提示词怎么描述那张图：**说实话**是关键——骨架卡转录时拿到的
    通常是整页**原图**（擦除手写是按块在入库时才生成的），而"已擦除"这句假话会让模型
    以为手写已经被清掉。
    """
    if image_kind not in IMAGE_KINDS:
        raise ValueError(f"认不出的图片种类：{image_kind!r}；可选：{', '.join(IMAGE_KINDS)}")
    box = block.get("bbox_norm")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        where = "、".join(f"{float(value):.3f}" for value in box)
        where_text = f"整页归一化边界 [x, y, w, h] = [{where}]"
    else:
        where_text = "这一块的边界读不出来（没有 bbox_norm）"

    text = (
        f"这一块（一道题）的位置：{where_text}。\n"
        f"{IMAGE_KINDS[image_kind]}\n"
        "请只读**这一块**里的题面。\n"
    )
    if ocr_text.strip():
        text += (
            "\n本地 OCR 草稿（**可能有错，以图为准**；它可能是整页的，"
            "与本块无关的行请忽略）：\n"
            f"```\n{ocr_text.strip()}\n```\n"
        )
    text += "\n请按系统提示里的字段输出那一个 JSON 对象。"

    return [
        {"role": "system", "content": TRANSCRIPT_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]


def _as_text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_list_of_text(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def parse_transcript(text: str) -> dict:
    """模型原文 → 结构化转录（**从不抛异常**：读不出来是"这一块没读成"，不是调用失败）。

    与 `intake_client.parse_semantics` 同一条纪律：抠不出 JSON、或字段形状不对，
    **把模型原话留下**（截断），不许只说一句"解析失败"。
    """
    from .model_client import extract_json

    raw = text if isinstance(text, str) else ""
    empty = {
        "transcript": None, "options": [], "question_no": None, "standard_answer": None,
        "topics": [], "error_causes": [], "unreadable": [], "notes": None,
        "confidence": None, "parsed": False,
    }
    try:
        data = extract_json(raw)
    except ValueError:
        return {**empty, "reason": raw.strip()[:300] or "（模型返回了空响应）"}
    if not isinstance(data, dict):
        return {**empty, "reason": f"模型返回的不是一个 JSON 对象：{str(data)[:200]}"}

    options = []
    for item in data.get("options") or []:
        if isinstance(item, dict):
            label = _as_text(item.get("label"))
            body = _as_text(item.get("text"))
            if label or body:
                options.append({"label": label or "", "text": body or ""})

    answer = data.get("standard_answer")
    standard = None
    if isinstance(answer, dict):
        value = _as_text(answer.get("value"))
        if value:
            confidence = answer.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                confidence = None
            standard = {"value": value, "confidence": confidence}

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
            or not 0.0 <= float(confidence) <= 1.0:
        confidence = None                      # 记一个假数字比记 None 更坏

    question_no = data.get("question_no")
    if not isinstance(question_no, (int, str)) or isinstance(question_no, bool):
        question_no = None

    transcript = _as_text(data.get("transcript"))
    return {
        "transcript": transcript,
        "options": options,
        "question_no": question_no,
        "standard_answer": standard,
        "topics": _as_list_of_text(data.get("topics")),
        "error_causes": _as_list_of_text(data.get("error_causes")),
        "unreadable": _as_list_of_text(data.get("unreadable")),
        "notes": _as_text(data.get("notes")),
        "confidence": confidence,
        "parsed": transcript is not None,
        "reason": None if transcript is not None else "模型没有给出 transcript",
    }


def transcribe(*, block: dict, image_url: str, config, runs_dir, ocr_text: str = "",
               image_kind: str = "page", transport=None, sleep=None, env=None,
               clock=None) -> dict:
    """问一次模型并把身份贴进答案（与 `HttpSemantics.__call__` 同一形状）。

    **调用失败抛 `ModelUnavailable`**（调用方据此报 502、什么都不写）；解析不出来**不抛**，
    它返回的是"这一块没读成"＋模型原话。
    """
    import time

    from .intake_client import _with_identity

    messages = transcript_messages(block=block, image_url=image_url, ocr_text=ocr_text,
                                   image_kind=image_kind)
    call = chat(config, messages, tag=TRANSCRIBE_TAG, runs_dir=runs_dir,
                transport=transport, sleep=sleep or time.sleep, env=env, clock=clock)
    return _with_identity(parse_transcript(call.text), call)


def apply_to_card(card: dict, result: dict) -> list[str]:
    """把一份转录结果写进卡（**只填读出来的**，读不出来的字段一律不动）。

    返回**真的改了的字段名**（空列表＝什么都没写）。这条纪律与页资源的 PATCH 同源：
    "没读到"不等于"清空"——把 `standard_answer` 写成 `null` 会覆盖掉人后来补的答案。
    """
    changed = []

    def put(path, value):
        node = card
        for key in path[:-1]:
            node = node.setdefault(key, {})
        if node.get(path[-1]) != value:
            node[path[-1]] = value
            changed.append(".".join(path))

    if result.get("transcript"):
        put(("problem", "transcript"), result["transcript"])
    if result.get("options"):
        put(("problem", "options"), result["options"])
    if result.get("standard_answer"):
        put(("standard_answer", "value"), result["standard_answer"]["value"])
        put(("standard_answer", "confidence"), result["standard_answer"].get("confidence"))
    if result.get("topics"):
        put(("topics",), result["topics"])
    if result.get("error_causes"):
        put(("error_causes",), result["error_causes"])

    for key in ("provider", "model", "run_id"):
        if result.get(key) is not None:
            put(("provenance", key), result[key])
    if result.get("notes"):
        put(("provenance", "model_notes"), result["notes"])
    if result.get("unreadable"):
        put(("provenance", "unreadable"), result["unreadable"])
    return changed
