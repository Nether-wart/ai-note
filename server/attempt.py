"""写端点：**作答进 → 判定出 → 回写**（#5，契约 §10.1 的第一种形态）。

`POST /api/attempt/<pid>`，body `{"channel": "screen", "answer": "<作答文本>"}`。

同一个路径还收**第三种形态**——定点修正 `{attempt_at, error_causes?, verdict?}`（#6，
契约 §10.1）：那种形态只改既有那一次重做，不新建、不重跑判定、不调模型，实现全在
`server/amend.py`；这里只按**键**分流（带 `attempt_at` 的一律走它）。

职责分工写死在这里，一处都不许漂：

  · **客户端不碰判定**（spec #1 Implementation Decisions）。服务自己问模型、自己映射、
    自己记来源与置信度；`verdict`／`source`／`confidence`／`provider`／`model` 这些字段
    由客户端提交就直接 400 —— 第八轮那次事故正是把不该写的字段写进了卡里。
    （**定点修正形态例外地允许人给 `verdict`／`error_causes`**——那是 spec #1 US 16 的
    当场改判；审计字段仍然只有服务能写。）
  · **能不能自动判定只有一份实现**：`server/autojudge.reject_reason`（#3 立的）。
    三种拒绝理由在这里被**调用**，绝不重写；文案照它的原话。
  · **映射只有一份实现**：`server/judge.judgment_from_output`（#4 立的）。
  · **状态机只有一份实现**：`server/mastery.apply_attempt`（#6 的重算走同一个 `step`）。
    回写后索引重建（v0 索引每次请求现算，所以「重建」= 写盘之后立刻读一次，拿到新的 `built_at`）。
  · **调用失败不是判定**（编排裁决 D1）：`ModelUnavailable` → 502，这一次重做不留记录，
    也不改冷却；原型的 `die()` = `sys.exit(2)` 在这里一处都不许出现。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from . import cardstore
from . import autojudge, judge, warnings as warnings_mod
from .amend import AmendEndpoint
from .autojudge import REASONS
from .errors import bad_request, model_unavailable, not_auto_judgeable
from .judge_client import ModelUnavailable, extract_json
from .mastery import apply_attempt

# 屏幕重做这一种形态**只收这两个键**（契约 §10.1）。多一个键就 400：
# 静默忽略是「不许静默」最讨厌的那种做法。
SCREEN_FORM_KEYS = frozenset({"channel", "answer"})
# 这个端点的 body **显式上限**（最终修复 pass 作业单 2）。合法请求只有
# `{channel, answer}`（定点修正那三个键），64 KiB 给足余量；没有上限时一个 2MB 的
# 合法 JSON 会被整段读进内存、作答还会发给模型。#13 之后服务要经 Tailscale 给手机用，
# 所以这条闸必须在**进模型之前**（`Api.handle` 在路由到本端点前就判掉，D1/D9）。
MAX_BODY_BYTES = 64 * 1024
# 这些字段一旦从客户端出现，就是「客户端在替服务下判定」——硬规则，必须喊。
CLIENT_MUST_NOT_JUDGE = ("verdict", "source", "confidence", "provider", "model",
                         "overrode", "attempt_at", "evidence_image")


class AttemptEndpoint:
    """一次屏幕重做的完整处理。判定角色与时钟都是构造函数注入的接缝。"""

    def __init__(self, catalog, *, judge_call: Callable, threshold: float, clock,
                 provider: str | None = None, model: str | None = None) -> None:
        self.catalog = catalog
        self.judge_call = judge_call
        self.threshold = threshold  # 装载时已由 config 校验过，这里不再解释配置
        self.clock = clock
        # 只在 502 的 details 里用：这次本来想用哪个模型（#5 验收：模型失败要说清）
        self.provider = provider
        self.model = model
        # 同一个端点的第三种形态（#6）：定点修正只改既有那一次重做，不碰判定角色。
        # 回写用它那一份实现：原子替换只有一处。
        self.amend = AmendEndpoint(catalog, clock=clock, write_card=self._write_card)

    # ---------------------------------------------------------------- 入口

    def handle(self, pid: str, raw_body: bytes | str | None) -> tuple[dict, list[dict]]:
        """返回 `(data, warnings)`；失败一律抛 `ApiError`（由 HTTP 层收进信封）。

        同一个端点收两种形态（契约 §10.1）：带 `attempt_at` 的是**定点修正**（#6），
        只改既有那一次重做；其余走**屏幕重做**（#5），自己问模型、自己判定。
        形态由**键**分辨，不由「试一下」分辨——两者对同一个键的合法性判断不同。
        """
        body = _parse_body(raw_body)
        if "attempt_at" in body:
            return self.amend.handle(pid, body)

        answer = _screen_form(body)

        card = self.catalog.load_card(pid)  # id 非法 → 400；没有这张卡 → 404
        card_warnings = warnings_mod.card_warnings(card, self.catalog)

        reason = autojudge.reject_reason(card)
        if reason:
            # 验收第 1 条：三种理由的文案与码都取自唯一那份实现，原话不改写
            raise not_auto_judgeable(reason, REASONS[reason], pid=pid, warnings=card_warnings)

        standard = (card.get("standard_answer") or {}).get("value")
        warnings = list(card_warnings)
        try:
            call = self.judge_call(standard, answer)
        except ModelUnavailable as exc:
            # 验收第 5 条 + D1：502，结构化信封，且**不留任何记录**
            raise model_unavailable(str(exc), pid=pid, provider=self.provider,
                                    model=self.model) from exc

        try:
            parsed = extract_json(call.text)
        except ValueError:
            # 解析不出来也是一次判定：落向看不清（记下这次重做，不推进也不清零）。
            # 但必须显式喊出来——模型答非所问不该只体现在一句 note 里。
            parsed = call.text
            warnings.append({
                "code": "judge_output_unparsed",
                "message": f"模型输出不是一个可解析的 JSON 对象，落向看不清：{call.text[:200]}",
                "id": pid,
                # 与 warnings.py 一致：级别显式发出来，界面不靠猜（契约 §2）
                "level": "warning",
            })

        judgment = judge.judgment_from_output(
            parsed, threshold=self.threshold, provider=call.provider, model=call.model
        )
        if not judgment.should_record:
            # judgment_from_output 不会给出「没有判定」；真到这一步说明有人改坏了它。
            raise model_unavailable(judgment.note, pid=pid, provider=call.provider,
                                    model=call.model)

        readout = apply_attempt(
            card, judgment.verdict,
            confidence=judgment.confidence,
            source=judgment.source,
            at=self.clock(),
            channel="screen",
            judge_note=judgment.note,
            provider=call.provider,
            model=call.model,
        )
        self._write_card(pid, card)

        # 「索引重建」在 v0 是立刻读一次现算索引：它永远反映最新状态（ADR 0001）。
        index, _, _ = self.catalog.index()
        data = {
            "attempt": card["attempts"][-1],
            "mastery": {
                "state": readout["state"],
                "streak": readout["streak"],
                "last_attempt_at": card["mastery"]["last_attempt_at"],
                # 这一次判对**是否被冷却挡住**（proto 口径；判错也进冷却，那是设计）
                "cooling": readout["cooling"],
                "credited": readout["credited"],
                "gap_days": readout["gap_days"],
                "note": readout["note"],
            },
            "run_id": call.run_id,
            "index_rebuilt_at": index["built_at"],
        }
        return data, warnings

    # ---------------------------------------------------------------- 回写

    def _write_card(self, pid: str, card: dict) -> Path:
        """写回**读进来的那个文件**——走 `cardstore`。

        「路径只由 pid 算、内容不参与」那条规矩以前在这里与 `page_commit` 里**各有一份**，
        再加属性编辑就是第三份。多份实现的代价是漂移，不是多敲几行字。
        """
        return cardstore.write_card(self.catalog, pid, card)


# -------------------------------------------------------------------- 输入校验

def _parse_body(raw_body: bytes | str | None) -> dict:
    """body 必须是一个 JSON 对象。任何一种坏输入都 → 400（绝不是 500）。"""
    if raw_body is None or raw_body == b"" or raw_body == "":
        raise bad_request("请求体不能为空", hint='要一个 JSON 对象：{"channel":"screen","answer":"A"}',
                          param="body", value="空", allowed=["object"])
    if isinstance(raw_body, bytes):
        try:
            raw_body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise bad_request(f"请求体不是 UTF-8：{exc}", param="body", allowed=["object"]) from exc
    try:
        body = json.loads(raw_body)
    except ValueError as exc:
        raise bad_request(f"请求体不是合法 JSON：{exc}",
                          hint='要一个 JSON 对象：{"channel":"screen","answer":"A"}',
                          param="body", value=raw_body[:200], allowed=["object"]) from exc
    if not isinstance(body, dict):
        raise bad_request(f"请求体必须是一个 JSON 对象，拿到的是 {type(body).__name__}",
                          param="body", value=raw_body[:200], allowed=["object"])
    return body


def _screen_form(body: dict) -> str:
    """校验 `{channel:"screen", answer}` 并返回作答文本。"""
    unknown = sorted(set(body) - SCREEN_FORM_KEYS)
    if unknown:
        leak = [key for key in unknown if key in CLIENT_MUST_NOT_JUDGE]
        if leak:
            raise bad_request(
                "判定只能由服务自己做，客户端不许提交这些字段：" + " / ".join(leak),
                hint="屏幕重做只提交 {channel:'screen', answer:'…'}；改判与定点修正归 #6",
                param="body", forbidden=leak, unknown=unknown,
            )
        raise bad_request("这个形态只收 channel 与 answer，多了：" + " / ".join(unknown),
                          hint="屏幕重做只提交 {channel:'screen', answer:'…'}",
                          param="body", unknown=unknown)

    channel = body.get("channel")
    if channel != "screen":
        raise bad_request(
            f"通道非法：{channel!r}",
            hint="#5 只实现 'screen'（屏幕重做）；纸上重做与定点修正归 #6",
            param="channel", value=channel, allowed=["screen"],
        )

    answer = body.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        # 空白作答不是「错」：送进模型大概率判错、静默清零一次掌握计数。
        # 宁可在门口喊（不许静默），也不要凭空抹掉一道题的进度。
        raise bad_request(
            "作答不能为空",
            hint="answer 必须是一个非空字符串；选择题敲一个字母即可",
            param="answer", value=answer, allowed="非空字符串",
        )
    return answer
