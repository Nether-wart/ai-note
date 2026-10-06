"""审核那条路的**服务端那半**：谁该审 ＋「给这一张跑一次转录」。

**为什么先做 CLI**：转录跑不跑是**人**的决定（骨架卡等审核填），所以第一版不做自动流程，
也不急着定 HTTP 形状。先例是 `server.intake`／`server.subject_assign`／`server.probe`——
能用的东西先能用，界面随后接。

三条纪律：

1. **只读不猜**：卡上没有页绑定就明确失败（`page_binding_missing`），**不去"猜一页"**——
   猜错等于把一次转录记到别的题上。
2. **输入是整页原图 ＋ 这一块的框**：页文件里只有一张整页照片（擦除手写是按块在入库时才生成的），
   所以提示词按 `image_kind="page"` **说实话**（"手写可能还在"），不许说"已擦除"。
3. **`--apply` 才写卡**，而且只填读出来的字段（`transcribe.apply_to_card` 的纪律：
   "没读到"不等于"清空"）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import cardstore, pages, transcribe
from .model_client import ModelUnavailable
from .ocr import read as ocr_read
from .paths import default_runs_dir

#: 没有页绑定、没有块绑定时用的码（与警告码表同名同事实，不另造名字）。
BINDING_MISSING = "page_binding_missing"


def _created_key(card: dict) -> str:
    """排序键：按**时刻**排（`created_at` 可能是带偏移的 ISO 串，字符串比会错）。"""
    raw = (card.get("created_at") or "").strip()
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        return ""                      # 读不出的排最后（它们本来就进不了时间线）


def pending(catalog, *, limit: int | None = None) -> list[dict]:
    """还没转录的卡（`problem.transcript` 空），**由新到老**——与"细则"同一口径。

    只读：一条都不改。读不出来的卡**跳过并报出来**（不许安静地少一张）。
    """
    rows, broken = [], []
    for path in sorted(Path(catalog.problems_dir).glob("*.json")):
        pid = path.stem
        try:
            card = cardstore.read_card(catalog, pid)
        except (OSError, ValueError) as exc:
            broken.append({"id": pid, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(card, dict):
            broken.append({"id": pid, "reason": "不是 JSON 对象"})
            continue
        if ((card.get("problem") or {}).get("transcript") or "").strip():
            continue
        rows.append({
            "id": card.get("id") or pid,
            "subject": card.get("subject"),
            "created_at": card.get("created_at"),
            "page_id": pages.page_hash_from_image((card.get("source") or {}).get("page_image")),
        })
    rows.sort(key=_created_key, reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return rows, broken


def _fail(code: str, message: str, hint: str | None = None) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def transcribe_card(catalog, pid: str, *, config=None, role: str = "extract", ocr=None,
                    transport=None, apply: bool = False, runs_dir=None, env=None,
                    clock=None) -> dict:
    """给一张卡跑一次转录。**永远返回一份读数**（不抛异常），CLI 与将来的界面都照原样显示。

    `config` 不注入时按 `role` 从三层来源解析（设置文件 ＞ 环境变量 ＞ 预设默认），
    与真调用**同一条解析**。
    """
    from . import config as config_module
    from . import settings as settings_module

    try:
        cardstore.check_pid(pid)
    except cardstore.IllegalCardId:
        return _fail("bad_request", f"这不像一个题卡 id：{pid!r}", "id 取自索引，不要自己拼")
    card = cardstore.read_card(catalog, pid)
    if card is None:
        return _fail("problem_missing", f"没有这张题卡：{pid}", "id 取自索引")

    page_id = pages.page_hash_from_image((card.get("source") or {}).get("page_image"))
    if not page_id:
        return _fail(BINDING_MISSING, f"卡 {pid} 上没有可用的页绑定（`source.page_image`）",
                     "旧卡可能是这样；`python3 -m server.backfill --apply` 能补一部分，补不上就人工补")
    page, error = pages.read_page(catalog, page_id)
    if page is None:
        return _fail(BINDING_MISSING, f"页文件读不出来：{page_id}（{error}）",
                     "页文件不在就别猜——猜一页会把这次转录记到别的题上")
    block = next((item for item in (page.get("blocks") or [])
                  if item.get("card_id") == pid), None)
    if block is None:
        return _fail(BINDING_MISSING, f"页 {page_id} 里没有绑着卡 {pid} 的块",
                     "只有绑定的那一块才认得出「哪道题」——没绑定就先在页上确认归属")

    image_name = page.get("image")
    image_path = Path(catalog.pages_dir) / str(image_name or "")
    if not image_name or not image_path.is_file():
        return _fail("page_image_missing", f"页 {page_id} 的整页照片不在：{image_name!r}",
                     "页文件在、照片丢了：这条链断了，先把它补回来")

    if config is None:
        try:
            config = config_module.load_role_config(
                role, env, settings=settings_module.load_settings(catalog))
        except (ValueError, settings_module.SettingsError) as exc:
            return _fail("model_unavailable", str(exc), "端点／密钥／模型缺一样就装不起来")

    # 注入的引擎优先（测试走这条）；没注入就**按配置找一个**——没配就是"不可用"，
    # 不去猜那台机器开没开（`engine_from_config` 只读配置，不试连接）。
    if ocr is None:
        from .ocr_baidu import engine_from_config
        ocr = engine_from_config(catalog, env=env)
    ocr_result = ocr_read(ocr, image_path.read_bytes(), name=str(image_name))
    draft = transcribe.draft_for_block(ocr_result, block.get("bbox_norm"))

    from .intake_client import image_data_url

    try:
        result = transcribe.transcribe(
            block=block, image_url=image_data_url(image_path), config=config,
            runs_dir=runs_dir or default_runs_dir(), ocr_text=draft, image_kind="page",
            transport=transport, env=env, clock=clock,
        )
    except ModelUnavailable as exc:
        return _fail("model_unavailable", str(exc),
                     "这一次没问成：**什么都没写**（可以重试）")

    changed = []
    if apply:
        changed = transcribe.apply_to_card(card, result)
        if changed:
            cardstore.write_card(catalog, pid, card)

    return {
        "ok": True,
        "card_id": pid,
        "page_id": page_id,
        "block_id": block.get("id"),
        "image": str(image_name),
        "image_kind": "page",
        "ocr_engine": ocr_result.engine,
        "ocr_draft_chars": len(draft),
        "result": {
            "transcript": result.get("transcript"),
            "options": result.get("options"),
            "question_no": result.get("question_no"),
            "standard_answer": result.get("standard_answer"),
            "topics": result.get("topics"),
            "error_causes": result.get("error_causes"),
            "unreadable": result.get("unreadable"),
            "notes": result.get("notes"),
            "confidence": result.get("confidence"),
            "parsed": result.get("parsed"),
            "reason": result.get("reason"),
            "provider": result.get("provider"),
            "model": result.get("model"),
            "run_id": result.get("run_id"),
        },
        "applied": bool(apply),
        "changed": changed,
    }


#: 审核这条路**能写**的字段（闭集）。与 §10.7 的属性编辑**刻意分开**：
#: 那边管科目／考点／错因／审核状态，这边管**内容**（题面、选项、原解、正解、标准答案）。
REVIEWABLE = ("transcript", "options", "standard_answer", "topics", "error_causes",
              "original_solution", "correct_solution")


def write_reviewed(catalog, pid: str, payload: dict, *, mark_reviewed: bool = False,
                   clock=None) -> tuple[dict, list, list]:
    """把**人确认或改过的**内容写进卡 → `(card, changed, warnings)`。

    与模型那条路（`transcribe.apply_to_card`）的区别**只有一处，但是要紧的一处**：
    模型那条是"没读到就不动"，这条是"**没给就不动、给了空串就是清空**"——
    人能明确说"这里没有"（例如这道题就没有标准答案），而模型不能。

    校验与 §10.7 同源：认不出的字段 → 拒并点名闭集；错因不在词表 → 拒并给出可选值
    （词表本身不在 → 先收下再喊，与"建"那条同一口径）。
    """
    from datetime import datetime, timezone

    from .problem_edit import ProblemEditError, _vocab_error_causes

    if not isinstance(payload, dict) or not payload:
        raise ProblemEditError("bad_request", "这一次请求没有要写的内容",
                               hint=f"至少要给一项：{', '.join(REVIEWABLE)}",
                               details={"param": "body", "allowed": list(REVIEWABLE)})
    for field in payload:
        if field not in REVIEWABLE:
            raise ProblemEditError(
                "problem_field_not_editable",
                f"{field} 不是审核这条路能写的字段",
                hint=("这条路人写的是**内容**（题面／选项／原解／正解／标准答案）；"
                      "科目、审核状态那些走属性编辑（§10.7）"),
                details={"param": field, "allowed": list(REVIEWABLE)},
            )

    card = cardstore.read_card(catalog, pid)
    if card is None:
        return None, [], []

    warnings: list = []
    changed: list = []

    def put(path, value):
        node = card
        for key in path[:-1]:
            node = node.setdefault(key, {})
        if node.get(path[-1]) != value:
            node[path[-1]] = value
            changed.append(".".join(path))

    if "transcript" in payload:
        value = payload["transcript"]
        if value is not None and not isinstance(value, str):
            raise ProblemEditError("bad_request", "transcript 得是字符串或 null",
                                   details={"param": "transcript"})
        # `""` ＝ **显式清空**（人可以说"这里没有"），`null`/不给 ＝ 不动
        if value is not None:
            put(("problem", "transcript"), value)

    if "options" in payload:
        options = payload["options"]
        if options is not None:
            if not isinstance(options, list):
                raise ProblemEditError("bad_request", "options 得是列表或 null",
                                       details={"param": "options"})
            clean = []
            for item in options:
                if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                    raise ProblemEditError("bad_request",
                                           "options 里每一项要是 {label, text}",
                                           details={"param": "options"})
                clean.append({"label": str(item.get("label") or ""), "text": item["text"]})
            put(("problem", "options"), clean)

    if "standard_answer" in payload:
        answer = payload["standard_answer"]
        if answer is None:
            pass
        elif not isinstance(answer, dict) or not isinstance(answer.get("value"), str):
            raise ProblemEditError("bad_request",
                                   "standard_answer 得是 {value, confidence} 或 null",
                                   details={"param": "standard_answer"})
        else:
            confidence = answer.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                confidence = None                       # 记一个假数字比记 None 更坏
            put(("standard_answer", "value"), answer["value"])
            put(("standard_answer", "confidence"), confidence)

    if "topics" in payload:
        topics = payload["topics"]
        if not isinstance(topics, list) or any(
                not isinstance(item, str) or not item.strip() for item in topics):
            raise ProblemEditError("bad_request", "topics 得是非空字符串的列表",
                                   details={"param": "topics"})
        put(("topics",), list(dict.fromkeys(item.strip() for item in topics)))

    if "error_causes" in payload:
        causes = payload["error_causes"]
        if not isinstance(causes, list) or any(
                not isinstance(item, str) or not item.strip() for item in causes):
            raise ProblemEditError("bad_request", "error_causes 得是非空字符串的列表",
                                   details={"param": "error_causes"})
        causes = list(dict.fromkeys(item.strip() for item in causes))
        allowed, vocab_warnings = _vocab_error_causes(catalog)
        warnings.extend(vocab_warnings)
        if allowed is not None:
            unknown = [cause for cause in causes if cause not in allowed]
            if unknown:
                raise ProblemEditError("error_cause_unknown",
                                       f"错因不在受控词表里：{', '.join(unknown)}",
                                       hint=f"可选：{'、'.join(allowed)}",
                                       details={"param": "error_causes", "unknown": unknown,
                                                "allowed": allowed})
        put(("error_causes",), causes)

    for field, keys in (("original_solution",
                         ("present", "original_answer", "transcript", "correction_transcript")),
                        ("correct_solution", ("text", "source"))):
        if field not in payload:
            continue
        spec = payload[field]
        if spec is None:
            continue
        if not isinstance(spec, dict):
            raise ProblemEditError("bad_request", f"{field} 得是对象或 null",
                                   details={"param": field})
        unknown = [key for key in spec if key not in keys]
        if unknown:
            raise ProblemEditError("bad_request",
                                   f"{field} 上有认不出的键：{', '.join(sorted(unknown))}",
                                   hint=f"只认 {', '.join(keys)}", details={"param": field})
        for key, value in spec.items():
            if value is not None and not isinstance(value, (str, bool)):
                raise ProblemEditError("bad_request", f"{field}.{key} 得是字符串／布尔／null",
                                       details={"param": f"{field}.{key}"})
            put((field, key), value)

    if mark_reviewed:
        status = "reviewed"
        node = card.get("review") or {}
        if node.get("status") != status:
            # **只动这两个键**：`review` 上还可能有 `review_reopened_because` 之类，
            # 整份替换会把它们静默抹掉（这一课在属性编辑那边已经吃过一次）。
            card["review"] = {
                **{key: value for key, value in node.items()
                   if key not in ("status", "reviewed_at")},
                "status": status,
                "reviewed_at": (clock() if clock else datetime.now(timezone.utc)).isoformat(),
            }
            changed.append("review.status")

    if changed:
        cardstore.write_card(catalog, pid, card)
    return card, changed, warnings


def main(argv: list[str] | None = None) -> int:
    """`python3 -m server.review list [--limit N]` ／ `python3 -m server.review <题卡 id> [--apply]`"""
    parser = argparse.ArgumentParser(prog="python3 -m server.review", description=__doc__)
    parser.add_argument("target", help="`list` 或一个题卡 id")
    parser.add_argument("--data", default=None, help="数据目录（默认跟其它 CLI 一致）")
    parser.add_argument("--apply", action="store_true", help="真的把转录写进卡（默认只看）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--role", default="extract")
    args = parser.parse_args(argv)

    from .catalog import Catalog

    data_dir = args.data or str(Path.cwd() / "data")
    catalog = Catalog(data_dir)

    if args.target == "list":
        rows, broken = pending(catalog, limit=args.limit)
        if not rows:
            print("没有待转录的卡。")
        for row in rows:
            print(f"{row['id']}  {row['subject'] or '未归类'}  {row['created_at'] or '（没有时刻）'}"
                  f"  页 {row['page_id'] or '（没有绑定）'}")
        if broken:
            print(f"\n读不出来的卡 {len(broken)} 张（**没有被安静地丢掉**）：", file=sys.stderr)
            for item in broken:
                print(f"  {item['id']}: {item['reason']}", file=sys.stderr)
        return 0

    readout = transcribe_card(catalog, args.target, apply=args.apply, role=args.role)
    print(json.dumps(readout, ensure_ascii=False, indent=2))
    if not readout["ok"]:
        print(f"\n没成：{readout['error']['message']}", file=sys.stderr)
        return 1
    if args.apply:
        print(f"\n写进卡的字段：{', '.join(readout['changed']) or '（没有可写的——没读出来）'}")
    else:
        print("\n（这是预演：加 `--apply` 才写卡）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
