"""题卡里的图片路径 → 磁盘上真实的文件，以及它能被服务的 URL。

**一处实现**：读一题与读图片都问这个模块，所以「卡里记了什么」与「实际能取到什么」
永远对得上。契约 §7.3 的安全规则也落在这里：只认 basename、只认 assets 目录内的文件，
所以卡里写 `../../etc/passwd` 也拿不到东西。
"""

from __future__ import annotations

import re
from pathlib import Path

KINDS = ("original", "clean", "mask")
_NAME_RE = re.compile(r"[\w.\-]+")
_SUFFIX_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def recorded_path(card: dict, kind: str) -> str | None:
    """题卡里记的图片路径（可能是相对仓库根的 `data/assets/x.png`）。没有就 None。"""
    problem = card.get("problem") or {}
    if kind == "original":
        return problem.get("image")
    if kind == "clean":
        return problem.get("clean_image")
    if kind == "mask":
        # 掩膜不是卡里的字段：约定文件名与擦除图同生共死（沿用 proto 的命名）。
        if problem.get("clean_image") or (problem.get("clean") or {}).get("clean_image"):
            return f"{(card.get('id') or '')}-cleanmask.png"
        return None
    raise ValueError(f"未知的图片类型：{kind!r}（只能是 {' / '.join(KINDS)}）")


def asset_file(catalog, card: dict, kind: str) -> Path | None:
    """能真正服务出去的文件；没有就 None。"""
    recorded = recorded_path(card, kind)
    if not recorded:
        return None
    name = Path(recorded).name
    if not _NAME_RE.fullmatch(name) or name != recorded.split("/")[-1]:
        return None
    path = catalog.assets_dir / name
    if path.suffix.lower() not in _SUFFIX_TYPES:
        return None
    return path if path.is_file() else None


def content_type_for(path: Path) -> str:
    return _SUFFIX_TYPES.get(path.suffix.lower(), "application/octet-stream")


def available_kinds(catalog, card: dict) -> list[str]:
    """实际能服务的类型：**URL 记了 且 文件真的在**，不是卡里声明的那几个。"""
    return [k for k in KINDS if asset_file(catalog, card, k)]


def image_urls(catalog, card: dict) -> dict[str, str | None]:
    """契约 §3.1 的 `images`。取不到的那一类给 `null`，绝不给出一个必然 404 的 URL。"""
    pid = card.get("id") or ""
    return {
        k: (f"/api/problem/{pid}/image/{k}" if asset_file(catalog, card, k) else None)
        for k in KINDS
    }
