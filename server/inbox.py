"""收件目录：录入的唯一入口（ADR 0007 第 4 条，契约 §10.3）。

录入的定义收敛成一句话——**往收件目录放一个文件**。三条路（电脑拖拽／手机上传页／
同步盘目录）之后走**同一条**管道：页 → 切分 → 审核队列。

这个模块负责这条管道**在切分实现之前**能负责的那一段，并且把不能负责的那一段
**说出来**（ADR 0007 第 6 条「不许静默」）：

- 收：把上传的照片写进收件目录。文件名取内容哈希前 12 位（与页照片的命名同一套口径），
  所以同一张照片重复上传不会堆出两份；原始文件名只留在响应里给人看。
- 管道接缝：`segmenter` 是一个可注入的 `(照片路径) -> 块列表`。
  **不注入时**必须显式报「切分不可用」，`blocks` 给 `null`——空列表会被读成
  「切出来 0 块」，那是一个假结果。**绝不允许**返回假的块列表、假的红笔统计、
  假的「已入库」来让界面看起来能跑。
- 扫：目录监视（`inotify`）在同步盘与某些挂载上不可靠（ADR 0007 的待验证项），
  所以「扫一遍收件目录」是一个**手动等价入口**，不是装饰。它找到什么、没处理什么，
  都逐条报出来。
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import re
from email.parser import BytesParser
from pathlib import Path

from . import assets
from .errors import bad_request

# 一次请求的字节上限。手机原图通常 2–8MB；给足余量，同时挡住「把磁盘读爆」这一类。
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

# 照片该有的后缀。**不是白名单**：不认识的照收，但会喊一声（同步盘里什么都会掉进来）。
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif",
                            ".gif", ".bmp", ".tif", ".tiff"})

_SEGMENTATION_MESSAGE = (
    "切分尚未实现（#10）：照片已经收进收件目录，但这次没有块列表、没有红笔统计，"
    "也没有生成任何题卡（入库归 #9/#15）"
)
_COMMIT_MESSAGE = (
    "入库（按块生成题卡并回写绑定）归 #9/#15：这次没有生成任何题卡"
)
VALID_SUFFIX = re.compile(r"\.[a-z0-9]{1,8}\Z")


# ------------------------------------------------------------------ 目录


class Inbox:
    """一个收件目录。它唯一的写入是「放一个新文件进去」。"""

    def __init__(self, directory: Path | str, *, segmenter=None) -> None:
        self.dir = Path(directory)
        self.segmenter = segmenter

    def ensure(self) -> bool:
        """确保目录在。返回是否**新建**了它（新建这件事要说出来）。"""
        if self.dir.is_dir():
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        return True

    def files(self) -> list[Path]:
        if not self.dir.is_dir():
            return []
        return sorted(p for p in self.dir.iterdir() if p.is_file())

    def store(self, name: str, blob: bytes) -> dict:
        """把一张照片放进收件目录。文件名 = 内容哈希，所以重复上传天然去重。"""
        digest = hashlib.sha256(blob).hexdigest()
        stored_as = f"{digest[:12]}{_suffix_of(name)}"
        path = self.dir / stored_as
        already = path.is_file()
        if not already:
            # 先写真名再改名，避免监视到半个文件（目录监视将来接手 #13 的第三路）。
            tmp = path.with_name(f".{stored_as}.part")
            tmp.write_bytes(blob)
            tmp.replace(path)
        return {
            "name": _display_name(name),
            "stored_as": stored_as,
            "bytes": len(blob),
            "sha256": digest,
            "content_type": content_type_of(stored_as),
            "already_present": already,
        }


def _suffix_of(name: str) -> str:
    suffix = Path(name or "").suffix.lower()
    return suffix if VALID_SUFFIX.fullmatch(suffix) else ""


def _display_name(name: str) -> str:
    """只留 basename：原始文件名是给人看的，不许把它当路径用。"""
    return Path((name or "").replace("\\", "/")).name or "未命名"


def content_type_of(name: str) -> str:
    return assets.content_type_for(Path(name))


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ 管道


def segmentation_status(segmenter) -> dict:
    """切分可不可用。**不可用时要有一句给人看的原话**，不许静默降级。"""
    if segmenter is None:
        return {"available": False, "reason": "not_implemented",
                "message": _SEGMENTATION_MESSAGE}
    return {"available": True, "reason": None, "message": None}


def commit_status() -> dict:
    """入库（按块生成题卡 + 回写绑定）归 #9/#15。这里必须说出来。"""
    return {"available": False, "reason": "not_implemented", "message": _COMMIT_MESSAGE}


def run_pipeline(inbox: Inbox, pages: list[dict], segmenter) -> tuple[dict, list[dict]]:
    """把（已经收进收件目录的）页喂进管道，返回 `(pipeline, warnings)`。

    `pages` 的元素要带 `page_index` 与 `stored_as`。切分不可用时每页的 `blocks` 是
    `null`（不是 `[]`）——「还没切」与「切出来 0 块」是两件不同的事。
    """
    status = segmentation_status(segmenter)
    out_pages: list[dict] = []
    for page in pages:
        blocks = None
        if status["available"]:
            blocks = segmenter(inbox.dir / page["stored_as"])
        out_pages.append({"page_index": page["page_index"],
                          "stored_as": page["stored_as"], "blocks": blocks})
    warnings = []
    if not status["available"]:
        warnings.append({"code": "segmentation_not_implemented",
                         "message": status["message"], "id": None, "level": "warning"})
    pipeline = {
        "segmentation": status,
        "commit": commit_status(),
        "pages": out_pages,
        "committed": False,   # #13 不写题卡：入库归 #9/#15
    }
    return pipeline, warnings


# ------------------------------------------------------------------ 上传


def parse_multipart(body: bytes, content_type: str) -> list[dict]:
    """解析 `multipart/form-data`（浏览器 `FormData` 的形状）。

    用手写正则切 boundary 容易在二进制里切错，所以走标准库 `email`——
    它认 `Content-Disposition` 与二进制 payload，且不引入第三方依赖（契约 §11：`server/` 零依赖）。
    """
    if not (content_type or "").lower().startswith("multipart/form-data"):
        raise bad_request(
            f"上传必须用 multipart/form-data，收到的是 {content_type!r}",
            hint="浏览器 FormData 会自己带上它；curl 用 -F file=@照片.png",
            param="Content-Type", value=content_type, allowed=["multipart/form-data"],
        )
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    try:
        message = BytesParser(policy=email.policy.default).parsebytes(header + body)
    except Exception as exc:
        raise bad_request(
            f"multipart body 解析失败：{exc.__class__.__name__}: {exc}",
            param="body", value=f"<{len(body)} 字节>",
        ) from exc
    if not message.is_multipart():
        raise bad_request(
            "multipart body 里没有分段（boundary 不对？）",
            param="Content-Type", value=content_type, allowed=["multipart/form-data"],
        )
    parts: list[dict] = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        parts.append({
            "field": part.get_param("name", header="content-disposition"),
            "name": part.get_filename() or "",
            "content_type": part.get_content_type(),
            "blob": part.get_payload(decode=True) or b"",
        })
    return parts


def accept(inbox: Inbox, body: bytes, content_type: str, segmenter) -> tuple[dict, list, list]:
    """`POST /api/inbox` 的全部逻辑。返回 `(data, warnings, skipped)`。"""
    parts = [p for p in parse_multipart(body, content_type) if p["field"] == "file"]
    if not parts:
        fields = sorted({p["field"] or "?" for p in parse_multipart(body, content_type)})
        raise bad_request(
            "这次上传里没有一个名叫 file 的文件",
            hint="multipart 里每个文件都应该是 name=\"file\"；"
                 "手机上传页与 curl -F file=@照片.png 都会这么发",
            param="file", value=fields, allowed=["file"],
        )

    inbox.ensure()
    warnings: list[dict] = []
    skipped: list[dict] = []
    received: list[dict] = []
    for index, part in enumerate(parts):
        if not part["blob"]:
            skipped.append({"code": "inbox_part_empty",
                            "message": f"第 {index + 1} 个 part（{part['name'] or '没有文件名'}）"
                                       f"是空的，没有收进收件目录",
                            "id": None})
            continue
        item = inbox.store(part["name"], part["blob"])
        item["page_index"] = len(received)
        received.append(item)
        if item["already_present"]:
            warnings.append({
                "code": "already_in_inbox",
                "message": f"{item['name']} 的内容收件目录里已经有了（{item['stored_as']}），"
                           f"没有重复写一份",
                "id": None, "level": "hint",
            })
        if Path(item["stored_as"]).suffix not in IMAGE_SUFFIXES:
            warnings.append({
                "code": "unexpected_file_type",
                "message": f"{item['name']} 的后缀不是已知的照片后缀"
                           f"（{'/'.join(sorted(IMAGE_SUFFIXES))}）→ 收下了，但请确认这是照片",
                "id": None, "level": "warning",
            })

    if not received:
        raise bad_request(
            "这次上传里没有一个非空的文件",
            hint="选一张照片或一个文件夹再试；空文件不会被收进收件目录",
            param="file", value=[p["name"] for p in parts],
        )

    pipeline, pipe_warnings = run_pipeline(inbox, received, segmenter)
    grouping = {
        "kind": "single_page" if len(received) == 1 else "multi_page",
        "count": len(received),
        "note": "一个文件 = 一页；一次传多个（一个文件夹）= 多页，按上传顺序排",
    }
    data = {
        "received": received,
        "grouping": grouping,
        "inbox": {"dir": str(inbox.dir), "files": len(inbox.files())},
        "pipeline": pipeline,
    }
    return data, warnings + pipe_warnings, skipped


# ------------------------------------------------------------------ 扫描


def scan(inbox: Inbox, segmenter) -> tuple[dict, list, list]:
    """`POST /api/inbox/scan`：目录监视失效时的**手动等价入口**。

    它做监视会做的那件事——认出现在收件目录里有哪些照片——并逐条说清
    「找到了但没处理，为什么」。它不假装处理过。
    """
    created = inbox.ensure()
    status = segmentation_status(segmenter)
    found: list[dict] = []
    for path in inbox.files():
        if path.name.startswith("."):
            continue  # 写到一半的临时文件不算一页
        blocked = (not status["available"]) or True  # 入库归 #9/#15，现在一律没走完管道
        found.append({
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": hash_file(path),
            "content_type": content_type_of(path.name),
            "status": "unprocessed",
            "reason": "segmentation_not_implemented" if not status["available"]
                      else "commit_not_implemented",
            "blocked": blocked,
        })

    pages = [{"page_index": i, "stored_as": item["name"]} for i, item in enumerate(found)]
    pipeline, warnings = run_pipeline(inbox, pages, segmenter)
    if created:
        warnings.insert(0, {
            "code": "inbox_created",
            "message": f"收件目录原来不存在，刚建了一个：{inbox.dir}",
            "id": None, "level": "hint",
        })
    data = {
        "inbox": {"dir": str(inbox.dir), "exists": inbox.dir.is_dir(),
                  "created": created, "files": len(found)},
        # 监视本身没实现（inotify 在同步盘上不可靠），所以这个手动入口是它的**等价物**，
        # 不是备份方案。这句话必须留在响应里，否则界面会以为有东西在盯着目录。
        "watch": {"implemented": False, "manual_entry": "/api/inbox/scan",
                  "message": "目录监视没有实现：inotify 在某些挂载与同步盘上不可靠"
                             "（ADR 0007 待验证项）。这个扫描就是它的手动等价入口"},
        "found": found,
        "pipeline": pipeline,
    }
    return data, warnings, []
