"""页资源的四个 HTTP 动作（契约 §10.2、spec #2「一个接缝：页资源」、工单 #14）。

spec #2 把界面的所有读写都收在**一个**接缝上：**模型调用与图像统计都在服务内部，
界面不碰照片处理**。四个动作：

| 动作 | 路由 | 归属 |
|---|---|---|
| **建** | `POST /api/page` | 照片进 → 存图 + 建页文件 + 跑切分（**#15 已落地**，`server/page_create.py`） |
| **改** | `PATCH /api/page/<id>` | **#14**（`server/page_edit.py`），本模块接线 |
| **重切** | `POST /api/page/<id>/resegment` | **本版（#17 / #31）起语义 = 「重置为预设」**：破坏性动作，要显式确认（`server/page_edit.reset_to_preset`），本模块接线；三态对照仍来自 #10 的 `classify_resegment` |
| **入库** | `POST /api/page/<id>/commit` | 按当前块列表生成题卡并回写绑定（**#15 已落地**，`server/page_commit.py`） |

**本模块自己不实现任何一条规则**，只做「HTTP 形状 ↔ 服务层」的翻译：

- 「改」的每条动作在 `page_edit` 里（取舍规则在 `intake` 里，#12）；
- 「重切」的三态在 `segmentation.classify_resegment` 里（匹配在 `pages.rebind` 里，#10）；
- 页 id 的校验在 `pages.is_page_id` 里，加载/拒绝路径在 `intake._load_page` 里（#12）。

四个动作现在**都落地了**（#14 两个、#15 两个）：建在 `server/page_create.py`、
入库在 `server/page_commit.py`——本模块只做 HTTP 形状的翻译，一条规则都不实现。

**模型失败 → 502**（D1）：切分是模型调用，`ModelUnavailable` 往上抛给 HTTP 层变成
502 `model_unavailable`，**页文件一个字节都不动**——重切本来也不写盘。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import assets, errors, ink, page_commit, page_create, page_edit, pages, segmentation
from .intake import _load_page
from .model_client import ModelUnavailable
from .warnings import _warn

# 「切分不可用」与 #13 的收件管道用同一个码（`server/inbox.py`）：同一个事实，
# 同一个码——不然界面上会有两个说法（契约 §8 的码表纪律）。
SEGMENTATION_NOT_IMPLEMENTED = "segmentation_not_implemented"

# 题卡文件读不了（契约 §8）：审计的 `card_files_readable` 用的是同一个码、同一件事——
# 「点名报出来，不是安静少一张」。重切用它是因为读不了的卡可能正是「人动过」的那张。
CARD_FILE_UNREADABLE = "card_file_unreadable"



def parse_json_body(body) -> dict:
    """PATCH 的 body → 对象。不是 JSON／不是对象 → **400**（输入不对，绝不是 500）。

    D1：拒绝一律 JSON 信封，带 `reason`。空 body 也走这里——一个 PATCH 没有 body
    就是输入错，而不是「改 0 条」。
    """
    raw = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")
    if not raw.strip():
        raise errors.bad_request(
            "这个请求没有 body：改一页要给出 `edits`",
            hint='body 形如 {"edits": [{"action": "move", "block_id": "b1", '
                 '"bbox_norm": [0.0, 0.0, 1.0, 0.2]}]}',
            param="body", value="")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise errors.bad_request(
            f"body 不是 JSON：{exc}",
            hint="改一页的 body 是一个 JSON 对象，形如 {\"edits\": [...]}",
            param="body", value=raw[:200],
        ) from exc
    if not isinstance(data, dict):
        raise errors.bad_request(
            f"body 不是一个 JSON 对象（拿到 {type(data).__name__}）",
            hint='形如 {"edits": [...]}；数组／字符串都不是',
            param="body", value=str(data)[:200],
        )
    return data


def _confirm_discard_manual(body) -> bool:
    """`resegment` 的 body 里带没带 `{"confirm_discard_manual": true}`。

    「重置为预设」是破坏性动作（#31），这一条问的是「人点没点确认」。**空 body、不是 JSON、
    不是对象都不算确认**，但**也不许崩**：没带就是没点，于是该 409 的地方照常 409
    （不许替人做不可逆的决定）。只认**严格的那个 `true`**——`"true"`／`1` 都是别人在猜。
    """
    raw = (body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray))
           else (body or ""))
    if not raw.strip():
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("confirm_discard_manual") is True


def _nothing_discarded(page: dict) -> dict:
    """没跑成的那两条路上回执里的 `discarded`：**什么都没被丢掉**（`manual_blocks` 是 0）。

    形状与成功那条路**一模一样**（契约 §10.2.1b），只是数字说的不是「将会丢掉多少」，
    而是「这一次没有重置，所以没丢东西」——把 409 那份粗估抄过来就是报了个假的。
    """
    return {**page_edit.discarded_manual_work(page), "manual_blocks": 0}


def _require_edits(payload: dict) -> list:
    """`edits` 必须是一个列表；里面的动作名必须在枚举里（400 带 `details.allowed`）。

    动作名在这里**先验一遍**再往下走：`page_edit` 那边也会拒绝不认识的动作，
    但那是「服务层的拒绝」（`changed=False` 的警告）；HTTP 层的输入错应当是 400，
    而且要在**碰盘之前**就拒掉（D9：拒绝就该一个字节都不动）。
    """
    edits = payload.get("edits")
    if not isinstance(edits, list):
        raise errors.bad_request(
            f"`edits` 必须是一个列表（拿到 {type(edits).__name__}）",
            hint='形如 {"edits": [{"action": "move", ...}]}；一次可以给多条，按顺序作用',
            param="edits", value=str(edits)[:200], allowed=list(page_edit.EDITABLE_ACTIONS),
        )
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise errors.bad_request(
                f"`edits` 的第 {index} 项不是一个对象（拿到 {type(edit).__name__}）",
                hint='每一项形如 {"action": "move", "block_id": "b1", "bbox_norm": [...]}',
                param="edits", value=str(edit)[:200],
                allowed=list(page_edit.EDITABLE_ACTIONS),
            )
        action = edit.get("action")
        if action not in page_edit.EDITABLE_ACTIONS:
            raise page_edit.bad_request_unknown_action(action)
    return edits


class PageEndpoint:
    """页资源的 HTTP 动作。构造时注入 `segmenter`（切分的模型接缝）。"""

    def __init__(self, catalog, *, segmenter=None) -> None:
        self.catalog = catalog
        self.segmenter = segmenter

    # ------------------------------------------------------------ 读（GET）

    def read(self, page_id: str) -> tuple[dict, list[dict]]:
        """`GET /api/page/<id>`：读出**整份页**（页文件 + 路径 + 照片名）。

        **为什么这条路由必须存在**（而不是拿「一次空修正的预演」当读来用）：
        界面要打开一页来改它，第一步就是读；而预演是 `PATCH`、要一个 body、
        语义是「先看看这么改会怎样」——把它当读用，等于让**读**依赖一条**写形状**的路由，
        下一个读代码的人会先花十分钟确认「它到底写不写」。读与写分开，是契约 §10.2.1b
        五个动作之外的第**六**个动作，形状与别的读端点一致（纯 GET、无 body）。

        返回 `{page_id, page_path, image, page}`：`page` 是页文件**原样**（块列表、
        `segmentation`、`removed_blocks`、`subject` 都在里面）。`image` 是**纯文件名**
        （照片与页文件同目录并列，D5）——整页照片由 `GET /api/page/<id>/image` 给。
        """
        page, read_error = pages.read_page(self.catalog, page_id)
        if read_error:
            raise errors.ApiError(
                500, "internal_error", read_error,
                hint="页文件在却读不了：先修好这个文件（索引与审计都会报它）",
                details={"what": "page", "id": page_id},
            )
        if page is None:
            raise errors.not_found(
                f"没有这一页：{page_id}",
                hint="页 id 是整页照片内容哈希的前 12 位；POST /api/page 建页时会给你",
                what="page", id=page_id,
            )
        return {
            "page_id": page_id,
            "page_path": str(pages.page_path(self.catalog, page_id)),
            "image": page.get("image"),
            "page": page,
        }, []

    # ------------------------------------------------------------ 改（PATCH）

    def edit(self, page_id: str, body) -> tuple[dict, list[dict]]:
        """`PATCH /api/page/<id>`：把 `edits` 按顺序作用在页文件上。

        `dry_run: true` → 预演（一个字节都不写）；默认真写（**验收 3**：每次修正都
        写回页文件，而不是只存在界面里）。
        """
        payload = parse_json_body(body)
        edits = _require_edits(payload)
        dry_run = bool(payload.get("dry_run") or payload.get("preview"))
        report = page_edit.apply_edit(self.catalog, page_id, edits, apply=not dry_run)
        # 响应体里不回整份页（页可能很大，而且界面要的是「我做了什么」）；
        # 但 `page` 留着——自检与测试要能对着它断言「重切的对照/改完的块是什么」。
        data = {**report, "dry_run": dry_run}
        return data, list(report.get("warnings") or [])

    # ------------------------------------------------------------ 重切

    def resegment(self, page_id: str, body=b"") -> tuple[dict, list[dict]]:
        """`POST /api/page/<id>/resegment`：**重置为预设**（#17 / #31 起语义变了）。

        语义不再是「重跑一次切分、只给对照」，而是一次**破坏性**动作：它会把人的劳动
        （人画的块、人拖过的边界、删过块的留痕）覆盖掉。所以：

        - 请求体带 `{"confirm_discard_manual": true}` 才算**确认**；这一页只要有人工改动
          （`page_edit.has_human_work`：块列表来源是 `manual`，或删过块），没带确认就是
          **409 `resegment_needs_confirmation`**，`details.discarded` 报清**将**丢掉什么。
          **空 body／不是 JSON 都不算确认**（也不崩）——没带就是没点（`_confirm_discard_manual`）。
        - 确认过（或本来就没有人工改动）才真跑：新预设落进页文件（`blocks`）、
          `segmentation.mode` 回到 `"model"`、没进新列表的旧块进 `removed_blocks[]`
          （`page_edit.reset_to_preset`），回执 `data.discarded` 用同一形状报**真的**丢了几块。
        - **为什么这个能力要留**：模型切分最坏的失败是把整页并成一块，那时从零手画十道题
          比重摇一次预设差得远；但它既然是「重置预设」，就必须按破坏性动作对待——
          **不许不声不响地覆盖人的劳动**。
        - 对手顺序有讲究：确认这一条判在**模型调用之前**（没确认就不该花钱，更不该先算一遍
          再问人）。

        `checks` / `reconciliation` **照旧跑**（三条确定性判据，只报不改）：这次改动只动
        语义与回执，那两键一个字段都不动。`wrote_cards` 恒为 `False`（重切从不建卡）。

        没注入切分器 = **切分不可用**（#10 的接缝口径，与 #13 的收件管道同一句话）：
        `blocks` 给 `null`（**不是 `[]`**——「还没切」与「切出 0 块」是两件事），
        并报 `segmentation_not_implemented`。**绝不编一个块列表**，也**一个字节都不写**
        （没有新预设可以重置）。
        """
        page = _load_page(self.catalog, page_id)
        page_edit.assert_page_payload_matches_id(self.catalog, page, page_id)

        # 破坏性动作先问人（#31）：它判在模型调用之前，也在写盘之前。
        if page_edit.has_human_work(page) and not _confirm_discard_manual(body):
            raise page_edit.resegment_needs_confirmation_error(
                page_id, page_edit.discarded_manual_work(page))

        if self.segmenter is None:
            return {
                "page_id": page_id,
                "segmentation": "unavailable",
                "ran": False,
                "blocks": None,          # 不是 []：没切 ≠ 切出 0 块
                "matches": None,
                "removed": None,
                "discarded": _nothing_discarded(page),
                "summary": None,
                "wrote_cards": False,
                "wrote_page": False,
            }, [_warn(
                SEGMENTATION_NOT_IMPLEMENTED,
                "这一趟没有可用的切分：服务没有接上切分那一层（#10 的模型接缝）"
                " → 没有块可以对照；**不返回假的块列表**",
                None, "warning")]

        # 模型调用失败（网络/超时/缺密钥/上游 5xx）→ `ModelUnavailable` 往上抛，
        # 由 HTTP 层变成 502（D1）。切分本来就不写盘，所以没什么可回滚的。
        try:
            outcome = self.segmenter(self._image_path(page))
        except ModelUnavailable as exc:
            # D1：把「没能问成」翻成 502，并把**哪一页**带进 `details`——
            # 调用方要能一眼看出是哪一页的切分没跑成（不许含糊）。
            raise errors.model_unavailable(str(exc), pid=page_id) from exc
        parsed = page_create.as_candidates(outcome)
        warnings = list(parsed.get("warnings") or [])
        if not parsed.get("parsed", True):
            # 答了话但解析不出块 → 明确的报告（不是「这一页没有题」）。没有新预设可重置，
            # 所以页文件仍然一个字节都不动。
            return {
                "page_id": page_id,
                "segmentation": "unparsed",
                "ran": True,
                "blocks": None,
                "matches": None,
                "removed": None,
                "discarded": _nothing_discarded(page),
                "summary": None,
                "message": parsed.get("message"),
                "wrote_cards": False,
                "wrote_page": False,
            }, warnings

        cards, card_warnings = self._cards()
        report = segmentation.classify_resegment(
            page, parsed["blocks"], cards={card["id"]: card for card in cards})
        # 对账（R1）：三条确定性判据跑在**这次重切给出的块列表**上——题号连续性／
        # 块重叠是纯几何，覆盖率读页文件旁边那张整页照片（读不出来就明说「没查」）。
        # 这两键**只报不改**：重置要改的是 `blocks`／`segmentation`／`removed_blocks` 三样。
        reconciliation = segmentation.reconcile_response(
            report["blocks"], self._ink_regions(page))
        # 预设回到页上时，新几何要一份**按照片真实像素尺寸推的** `bbox_px`（整页像素 xyxy）：
        # 与 `page_create._page_blocks` 是**同一件事**（那是新页的初值），所以复用那一处实现，
        # 不写第二份换算。推不出来（照片不在／读不了）就留空——像素框宁可空着，也不许拿
        # `bbox_norm` 反推一个假的（D5：`bbox_px` 是加过 pad 的盘上原值）。
        pixel_boxes = page_create._page_blocks(parsed["blocks"], self._image(page))
        for block, laid_out in zip(report["blocks"], pixel_boxes):
            block["bbox_px"] = laid_out["bbox_px"]
        # 确认过了（或本来就没有人工改动）→ **真的重置**：换块列表、来源回 `model`、
        # 丢掉的旧块进留痕。写盘路径仍然只由 page_id 决定（`save_page` 里那一道）。
        reset = page_edit.reset_to_preset(page, report)
        pages.save_page(self.catalog, reset["page"], page_id=page_id, apply=True)
        return {
            "page_id": page_id,
            "segmentation": "ran",
            "ran": True,
            "rejected": parsed.get("rejected") or [],
            "message": parsed.get("message"),
            "checks": reconciliation["checks"],
            "reconciliation": reconciliation["reconciliation"],
            "discarded": reset["discarded"],
            **report,
            # 这一次真的写了页文件（`report["wrote_page"]` 是那个**纯函数**自己的口径：
            # 它不写盘；写盘是这一层做的——两个数说的是两件事，别合并成一个）。
            "wrote_page": True,
        }, (warnings + card_warnings + list(report.get("warnings") or [])
            + reconciliation["warnings"])

    # ------------------------------------------------------------ 整页照片

    def image(self, page_id: str) -> tuple[bytes, str]:
        """`GET /api/page/<id>/image`：整页照片的字节（界面上画块框要它）。

        它是**页文件旁边**那张照片（D5：同名不同后缀），所以路径由页文件里的
        `image`（一个**纯文件名**）定，并再过一次不变式——与写回路径同一条纪律：
        **绝不由载荷内容决定读到哪**。

        「页在、照片不在」是**真矛盾**（页实体已经在场）→ 404 并说清缺的是哪一张，
        而不是给一张替身图（同 D4 的口径：不许回退成别的东西）。
        """
        page = _load_page(self.catalog, page_id)
        page_edit.assert_page_payload_matches_id(self.catalog, page, page_id)
        path = self._image_path(page)
        if not path.is_file():
            raise errors.not_found(
                f"页 {page_id} 的整页照片不在：{path}",
                hint="页文件与照片同目录并列（D5）；照片被删了这一页就没法画框了",
                what="page_image", id=page_id, image=str(page.get("image") or ""),
            )
        return path.read_bytes(), assets.content_type_for(path)

    # ------------------------------------------------------------ 建 / 入库

    def create(self, body, content_type: str) -> tuple[dict, list[dict]]:
        """`POST /api/page`（建）：照片 → 存图 + 建页文件 + 跑切分 + 红笔统计与建议去留。

        **规则不在这里**（`server/page_create.py: create_pages` 是唯一实现）：
        本模块只把 body／content_type 交给它。请求形状与 `POST /api/inbox` 一致
        （multipart、字段名 `file`），所以手机上传页与 curl 用同一套发法。
        """
        return page_create.create_pages(
            self.catalog, body=body, content_type=content_type, segmenter=self.segmenter)

    def commit(self, page_id: str) -> tuple[dict, list[dict]]:
        """`POST /api/page/<id>/commit`（入库）：按块列表生成题卡 + 回写绑定 + 重建索引。

        **规则不在这里**：谁该建卡、id 怎么分配、幂等怎么保证，全在
        `server/page_commit.py: commit_page`（唯一实现）。这一层只翻译 HTTP 形状。

        拒绝形状沿用 `intake._load_page` 与 `page_edit.assert_page_payload_matches_id`：
        页 id 非法/载荷不对账 → 400、页不在 → 404、页文件读不了 → 500，**一个字节都不写**。
        """
        return page_commit.commit_page(self.catalog, page_id)

    # ------------------------------------------------------------ 内部

    def _image_path(self, page: dict) -> Path:
        """整页照片的盘上位置：页文件与照片**同目录并列**（D5），所以 `image` 是纯文件名。

        `_load_page` 之后已经过 `assert_page_payload_matches_id`，所以这里可以放心拼——
        但仍然只拼**一个文件名**（`Path(image).name`），纵深防御。
        """
        return self.catalog.pages_dir / Path(str(page.get("image") or "")).name

    def _cards(self) -> tuple[list[dict], list[dict]]:
        """盘上的题卡 + 「哪些卡没读进来」的记账。

        读不了的卡**不许安静地 `continue`**（R4）：它可能正是「人动过、不许被重切改写」
        的那一张，丢掉它 = 这条对账查不全还不记账。按 §8 报 `card_file_unreadable`，
        点名到文件（`id` = 文件名主干，`message` 里带全路径）——判据与审计的
        `card_files_readable` 那一项**同一件事、同一个码**（`server/audit.py`）。
        """
        cards: list[dict] = []
        warnings: list[dict] = []
        for path in sorted(self.catalog.problems_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                warnings.append(_warn(
                    CARD_FILE_UNREADABLE,
                    f"题卡 {path.name} 读不了（{exc.__class__.__name__}: {exc}）→ "
                    f"这张卡**没有进**这次重切对账：「人动过的卡不许被改写」这一条对它没查，"
                    f"先修好这个文件（它可能是人动过的那一张）",
                    path.stem, "warning"))
                continue
            if not isinstance(card, dict):
                warnings.append(_warn(
                    CARD_FILE_UNREADABLE,
                    f"题卡 {path.name} 不是一个 JSON 对象 → 这张卡**没有进**这次重切对账："
                    f"「人动过的卡不许被改写」这一条对它没查",
                    path.stem, "warning"))
                continue
            if card.get("id"):
                cards.append(card)
        return cards, warnings

    def _image(self, page: dict):
        """页文件旁边那张整页照片 → 解码后的整页图；不在／读不了 → `None`。

        「读不出来」与「查过没问题」必须长得不一样：拿到 `None` 的调用方要么明说
        **这一条没查**（覆盖率对账），要么把那一项**留空**（像素框），不许冒充查过。
        """
        try:
            return ink.read_png(self._image_path(page))
        except (OSError, ink.UnsupportedImage):
            return None

    def _ink_regions(self, page: dict):
        """页文件旁边那张整页照片 → 墨迹区域（覆盖率对账的输入，契约 §8）。

        读不了 / 不在 → `None`：`check_coverage` 会明说**这一条没查**（hint），
        而不是冒充「没有未覆盖的墨迹」——「没查」与「查过没问题」必须长得不一样。
        """
        image = self._image(page)
        return ink.page_ink_regions(image) if image is not None else None


__all__ = ["PageEndpoint", "parse_json_body", "SEGMENTATION_NOT_IMPLEMENTED",
           "CARD_FILE_UNREADABLE", "ModelUnavailable"]
