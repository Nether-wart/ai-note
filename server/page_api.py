"""页资源的四个 HTTP 动作（契约 §10.2、spec #2「一个接缝：页资源」、工单 #14）。

spec #2 把界面的所有读写都收在**一个**接缝上：**模型调用与图像统计都在服务内部，
界面不碰照片处理**。四个动作：

| 动作 | 路由 | 归属 |
|---|---|---|
| **建** | `POST /api/page` | 照片进 → 存图 + 建页文件 + 跑切分（**#15 已落地**，`server/page_create.py`） |
| **改** | `PATCH /api/page/<id>` | **#14**（`server/page_edit.py`），本模块接线 |
| **重切** | `POST /api/page/<id>/resegment` | 跑切分 → 逐块**新增／保留**对照，**不写题卡**（#10 的 `classify_resegment`），本模块接线 |
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

from . import assets, errors, ink, page_commit, page_create, page_edit, segmentation
from .intake import _load_page
from .model_client import ModelUnavailable
from .warnings import _warn

# 「切分不可用」与 #13 的收件管道用同一个码（`server/inbox.py`）：同一个事实，
# 同一个码——不然界面上会有两个说法（契约 §8 的码表纪律）。
SEGMENTATION_NOT_IMPLEMENTED = "segmentation_not_implemented"



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

    def resegment(self, page_id: str) -> tuple[dict, list[dict]]:
        """`POST /api/page/<id>/resegment`：重跑切分 → 逐块**新增／保留**对照。

        **不写题卡、不写页文件**（spec #2 原话：「重切返回的是逐块对照」；
        #10 的 `classify_resegment` 把 `wrote_cards` / `wrote_page` 显式写进报告）。
        界面照 `matches[].state` 显示三态，而不是只显示「重切完成」。

        没注入切分器 = **切分不可用**（#10 的接缝口径，与 #13 的收件管道同一句话）：
        `blocks` 给 `null`（**不是 `[]`**——「还没切」与「切出 0 块」是两件事），
        并报 `segmentation_not_implemented`。**绝不编一个块列表。**
        """
        page = _load_page(self.catalog, page_id)
        page_edit.assert_page_payload_matches_id(self.catalog, page, page_id)

        if self.segmenter is None:
            return {
                "page_id": page_id,
                "segmentation": "unavailable",
                "ran": False,
                "blocks": None,          # 不是 []：没切 ≠ 切出 0 块
                "matches": None,
                "removed": None,
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
            # 答了话但解析不出块 → 明确的报告（不是「这一页没有题」）
            return {
                "page_id": page_id,
                "segmentation": "unparsed",
                "ran": True,
                "blocks": None,
                "matches": None,
                "removed": None,
                "summary": None,
                "message": parsed.get("message"),
                "wrote_cards": False,
                "wrote_page": False,
            }, warnings

        cards = {card.get("id"): card for card in self._cards() if card.get("id")}
        report = segmentation.classify_resegment(page, parsed["blocks"], cards=cards)
        # 对账（R1）：三条确定性判据跑在**这次重切给出的块列表**上——题号连续性／
        # 块重叠是纯几何，覆盖率读页文件旁边那张整页照片（读不出来就明说「没查」）。
        # **只报不改**：重切本来就不写盘（见 docstring）。
        reconciliation = segmentation.reconcile_response(
            report["blocks"], self._ink_regions(page))
        return {
            "page_id": page_id,
            "segmentation": "ran",
            "ran": True,
            "rejected": parsed.get("rejected") or [],
            "message": parsed.get("message"),
            "checks": reconciliation["checks"],
            "reconciliation": reconciliation["reconciliation"],
            **report,
        }, warnings + list(report.get("warnings") or []) + reconciliation["warnings"]

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

    def _cards(self) -> list[dict]:
        """盘上的题卡（`{id: 卡}` 的原料）。切分对账要用它判断「这张卡人动过没有」。"""
        cards: list[dict] = []
        for path in sorted(self.catalog.problems_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                # 读不了的卡不进对账：它由 `catalog` 的既有路径去喊（这里不重复报）。
                continue
            if isinstance(card, dict) and card.get("id"):
                cards.append(card)
        return cards

    def _ink_regions(self, page: dict):
        """页文件旁边那张整页照片 → 墨迹区域（覆盖率对账的输入，契约 §8）。

        读不了 / 不在 → `None`：`check_coverage` 会明说**这一条没查**（hint），
        而不是冒充「没有未覆盖的墨迹」——「没查」与「查过没问题」必须长得不一样。
        """
        try:
            return ink.page_ink_regions(ink.read_png(self._image_path(page)))
        except (OSError, ink.UnsupportedImage):
            return None


__all__ = ["PageEndpoint", "parse_json_body", "SEGMENTATION_NOT_IMPLEMENTED",
           "ModelUnavailable"]
