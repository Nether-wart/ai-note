#!/usr/bin/env python3
"""写入服务（第一版）：审核 + 掩膜编辑 + 活页纸排版。

三件事对应三处旧承诺：
- ADR 0001：所有写入只经过它；它从题目文件派生索引（GET /api/index，另存 data/index.json）。
- CONTEXT「审核」：擦除结果、原答/订正、标准答案、考点错因、标签提案都在这一个界面里过。
- CONTEXT「重做纸 / 版面格 / 页锚点」：/sheet 生成可打印的 A4 活页纸。

掩膜规格（自动框 ∪ 人工补 − 人工减）存进题卡，所以擦除**可重放**：改掩膜只重算，
不重新问模型；换分辨率也不会漂。

跑法：python3 proto/server.py --port 8765
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import slice as S

DATA, PROBLEMS, ASSETS, VOCAB = S.DATA, S.PROBLEMS, S.ASSETS, S.VOCAB
INDEX_PATH = DATA / "index.json"
LOCK = threading.Lock()

CELL_MM = 66.0          # 一页四格，每格高度；与 sheet 的页高对齐
CELLS_PER_PAGE = 4
PAGE_H_MM = 276.0       # 略小于 A4 内容高（277mm），留出取整余量，免得打印时多吐白页


# ---------------------------------------------------------------- 数据

def load_card(pid: str) -> dict:
    return json.loads((PROBLEMS / f"{pid}.json").read_text(encoding="utf-8"))


def save_card(pid: str, rec: dict):
    (PROBLEMS / f"{pid}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def default_cells(rec: dict) -> int:
    """版面格初值。键必须用题卡里的实际枚举（choice/fillin/solution），
    写中文键会静默退回默认值——这个坑真踩过。"""
    return {"choice": 1, "fillin": 2, "solution": 4}.get(rec["problem"]["type"], 2)


TYPE_CN = {"choice": "选择", "fillin": "填空", "solution": "解答"}
MASTERY_CN = {"in_pool": "在池", "graduated": "毕业"}
BATCHES = DATA / "batches"


def sort_key(rec: dict) -> str:
    """默认打印清单按「上次重做的先后、从早到晚」排。

    要用归一到 UTC 的时刻比，**不能比原始字符串**：卡里的 created_at 是 +08:00，
    重做时刻却是 UTC，字符串比较会把 06:31Z 排在 09:00Z 后面（真踩过）。
    """
    base = S._as_dt((rec.get("mastery") or {}).get("last_attempt_at")) or S._as_dt(rec.get("created_at"))
    return base.astimezone(timezone.utc).isoformat() if base else ""


def last_verdict(rec: dict):
    a = rec.get("attempts") or []
    return a[-1].get("verdict") if a else None


def attempt_brief(a: dict) -> dict:
    return {"at": (a.get("at") or "")[:10], "verdict": a.get("verdict"),
            "verdict_cn": S.VERDICT_CN.get(a.get("verdict"), a.get("verdict")),
            "channel_cn": S.CHANNEL_CN.get(a.get("channel"), a.get("channel")),
            "source": a.get("source"), "error_causes": a.get("error_causes") or [],
            "note": a.get("note")}


def card_warnings(rec: dict) -> list[str]:
    """审核字段的自检。它既拦保存、也在列表页暴露存量脏数据——因为"存下来的错答案"比"没存"更危险。

    这套检查是被一次真事故逼出来的：界面的一个静默 bug 把第一题的 原答/标准答案 写进了第二题，
    于是那张卡带着另一道题的标准答案被标成了“已审核”。
    """
    w = []
    t = rec["problem"]["type"]
    cn = TYPE_CN.get(t, t)
    std = ((rec.get("standard_answer") or {}).get("value") or "").strip()
    orig = (rec["original_solution"].get("original_answer") or "").strip()

    if not std:
        w.append("标准答案为空 → 不能走自动判定，只能人工确认")
    if t == "choice" and std and not re.fullmatch(r"[A-Da-d]", std):
        w.append(f"选择题的标准答案不是选项字母：{std!r}")
    if t != "choice" and re.fullmatch(r"[A-Da-d]", std):
        w.append(f"题型是{cn}，标准答案却只有一个字母 {std!r} ——像是把选择题的答案填到这道题上了")
    if t != "choice" and re.fullmatch(r"[A-Da-d]", orig):
        w.append(f"题型是{cn}，原答却是一个选项字母 {orig!r}")
    if orig and std and orig.upper() == std.upper():
        w.append(f"原答与标准答案相同（都是 {std!r}）→ 这是错题本，录进来的是做错的题；"
                 f"两者相同通常意味着订正被当成了原答")
    if not rec["topics"]:
        w.append("考点为空 → 考点是检索入口")
    if rec["review"]["status"] == "reviewed" and (not std or not rec["topics"]):
        w.append("已标为已审核，但标准答案或考点是空的")
    return w


def asset_url(rec: dict, key: str):
    p = rec["problem"].get(key)
    return f"/assets/{Path(p).name}" if p else None


def build_index() -> dict:
    """派生索引：随时可以从题目文件重建，不是数据的来源（ADR 0001）。"""
    items = []
    for f in sorted(PROBLEMS.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        clean = rec["problem"].get("clean") or {}
        pr = rec.get("print") or {}
        items.append({
            "id": rec["id"],
            "created_at": rec["created_at"],
            "type": rec["problem"]["type"],
            "transcript": rec["problem"]["transcript"],
            "options": rec["problem"].get("options") or [],
            "original_answer": rec["original_solution"].get("original_answer"),
            "correction": rec["original_solution"].get("correction_transcript"),
            "original_transcript": rec["original_solution"].get("transcript"),
            "present": rec["original_solution"].get("present"),
            "standard_answer": (rec.get("standard_answer") or {}).get("value"),
            "correct_solution": (rec.get("correct_solution") or {}).get("text"),
            "topics": rec["topics"],
            "error_causes": rec["error_causes"],
            "new_tag_proposals": rec.get("new_tag_proposals") or [],
            "review": rec["review"]["status"],
            "reviewed_at": rec["review"]["reviewed_at"],
            "mastery": rec["mastery"],
            "mastery_cn": MASTERY_CN.get(rec["mastery"].get("state"), rec["mastery"].get("state")),
            "streak": int(rec["mastery"].get("streak") or 0),
            "cooling": S.is_cooling(rec),
            "cooldown_days": S.cooldown_days_left(rec),
            "last_verdict": last_verdict(rec),
            "last_attempt_at": rec["mastery"].get("last_attempt_at"),
            "attempts": len(rec.get("attempts") or []),
            "attempt_log": [attempt_brief(a) for a in (rec.get("attempts") or [])][-6:],
            "sort_key": sort_key(rec),
            "cells": pr.get("cells") or default_cells(rec),
            "cells_source": pr.get("cells_source") or "default",
            "image": asset_url(rec, "image"),
            "clean_image": asset_url(rec, "clean_image"),
            "mask_url": f"/assets/{rec['id']}-cleanmask.png" if clean.get("clean_image") or rec["problem"].get("clean_image") else None,
            "clean_stats": {k: clean.get(k) for k in
                            ("boxes", "mask_px", "colored_px", "residual_px", "dropped_px") if k in clean},
            "health": clean.get("health"),
            "manual": clean.get("manual") or {"add": [], "drop": []},
            "boxes_norm": clean.get("boxes_norm") or [],
            "has_clean": bool(rec["problem"].get("clean_image")),
            "warnings": card_warnings(rec),
        })
    idx = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "count": len(items), "problems": items}
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    return idx


def topic_leaves() -> list[str]:
    d = json.loads((VOCAB / "topic-outline.seed.json").read_text(encoding="utf-8"))
    out: list[str] = []

    def walk(node, prefix):
        for ch in node.get("children") or []:
            name = f"{prefix}/{ch['id']}" if prefix else ch["id"]
            if ch.get("children"):
                walk(ch, name)
            else:
                out.append(name)

    walk(d, "")
    return out


def all_causes() -> list[str]:
    return json.loads((VOCAB / "error-causes.json").read_text(encoding="utf-8"))["错因"]


# ---------------------------------------------------------------- 排版

def pack_pages(items: list[dict]) -> list[list[tuple[dict, int]]]:
    """把题排进「一页四格」。整页题（4 格）独占一页。"""
    pages: list[list[tuple[dict, int]]] = []
    cur: list[tuple[dict, int]] = []
    used = 0
    for rec in items:
        n = max(1, min(CELLS_PER_PAGE, int(rec["cells"])))
        if n >= CELLS_PER_PAGE:
            if cur:
                pages.append(cur)
                cur, used = [], 0
            pages.append([(rec, CELLS_PER_PAGE)])
            continue
        if used + n > CELLS_PER_PAGE:
            pages.append(cur)
            cur, used = [], 0
        cur.append((rec, n))
        used += n
    if cur:
        pages.append(cur)
    return pages


def first(q: dict, key: str, default=None):
    """GET 参数一律是列表，取第一个。"""
    v = q.get(key)
    if isinstance(v, list):
        return v[0] if v else default
    return v if v else default


def sheet_items(idx: dict, q: dict) -> list[dict]:
    """默认打印清单：未毕业、且已脱离冷却的题，按上次重做的先后从早到晚。

    从未重做过的题以录入时间起算。两个开关：「包含未审核」「显示冷却中的题」——
    后者勾上就能先打刚录入或刚做过的题，这类重做照常记录，但不推进掌握。
    """
    items = [p for p in idx["problems"] if p["has_clean"] or p["review"] == "reviewed"]
    if not q.get("unreviewed"):
        items = [p for p in items if p["review"] == "reviewed"]
    if not q.get("cooling"):
        items = [p for p in items if not p["cooling"]]
    items = [p for p in items if p["mastery"].get("state") != "graduated"]
    return sorted(items, key=lambda p: p["sort_key"])


def new_batch(idx: dict, q: dict) -> dict:
    """生成一叠重做纸 = 一个**打印批次**（CONTEXT「打印批次」）。

    页锚点必须指向它，而不是"当前排版的哈希"：排版会随复习进度变化（毕业、脱离冷却、
    新录入都会改变分页），只有**存下来的批次**能让已经打印出去的那张纸永远指得回同一批题。
    这也是 CONTEXT 那句话的实现：页锚点本身不承担数据回写，所以与识别能力无关。
    """
    items = sheet_items(idx, q)
    pages = pack_pages(items)
    if not pages:
        return None                     # 空清单不落批次，免得留下一堆空的打印记录
    code = secrets.token_hex(2).upper()
    bid = f"b-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(2)}"
    rec = {"id": bid, "code": code,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "filters": {"unreviewed": bool(q.get("unreviewed")),
                       "cooling": bool(q.get("cooling")),
                       "gray": bool(q.get("gray"))},
           "pages": [{"no": i, "anchor": f"{code}-P{i}",
                      "ids": [r["id"] for r, _ in page],
                      "cells": [n for _, n in page]}
                     for i, page in enumerate(pages, 1)]}
    BATCHES.mkdir(parents=True, exist_ok=True)
    (BATCHES / f"{bid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return rec


def load_batch(bid: str):
    if not bid or not re.fullmatch(r"[\w.\-]+", str(bid)):
        return None
    f = BATCHES / f"{bid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def recent_batches(limit: int = 8) -> list[dict]:
    if not BATCHES.is_dir():
        return []
    out = []
    for f in BATCHES.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(out, key=lambda b: b.get("created_at") or "", reverse=True)[:limit]


def find_batch_by_code(code: str):
    for b in recent_batches(50):
        if (b.get("code") or "").upper() == (code or "").upper():
            return b
    return None


def resolve_anchor(text: str):
    """页锚点形如 3F2A-P1；只给批次码 3F2A 就是整批。"""
    t = (text or "").strip().upper()
    if not t:
        return None, None
    code, _, pno = t.partition("-")
    b = find_batch_by_code(code)
    if not b:
        return None, None
    return b, (int(pno) if pno.isdigit() else None)


def batch_ids(batch: dict, page_no=None) -> list[str]:
    pages = [p for p in batch["pages"] if not page_no or p["no"] == page_no]
    return [pid for p in pages for pid in p["ids"]]


def render_sheet(q: dict) -> str:
    idx = build_index()
    by_id = {p["id"]: p for p in idx["problems"]}
    batch = load_batch(first(q, "batch"))
    if batch:
        pages = [[(by_id[pid], cell) for pid, cell in zip(pg["ids"], pg["cells"]) if pid in by_id]
                 for pg in batch["pages"]]
        items = [p for page in pages for p, _ in page]
    else:
        items = sheet_items(idx, q)
        pages = pack_pages(items)

    gray = " gray" if (q.get("gray") or (batch and batch["filters"].get("gray"))) else ""
    out = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
           f'<title>错题重做纸（{len(items)} 题 / {len(pages)} 页）</title>',
           f'<style>{SHEET_CSS}</style></head><body class="sheet{gray}">']
    if not batch:
        out.append('<p class="empty">这是预览：<b>没有页锚点</b>，也没有打印批次，所以无法回写。'
                   '要真正打印并回写，请回清单页点「生成活页纸」——那会创建一个打印批次，'
                   '页锚点指向它。</p>')
    if not items:
        out.append('<p class="empty">没有可打印的题。重做纸只印**已审核**且未毕业、未在冷却期'
                   '的题；原型阶段可以勾「包含未审核」先试打。</p>')

    for i, page in enumerate(pages, 1):
        ids = [p["id"] for p, _ in page]
        if batch:
            pg = next((p for p in batch["pages"] if p["no"] == i), None)
            anchor = pg["anchor"] if pg else ""
            right = (f'页锚点 <b>{anchor}</b> · 批次 {batch["code"]}'
                     f' · 标这一页 {ANCHOR_BASE}/mark?anchor={anchor}')
        else:
            anchor, right = "", '（预览：无页锚点）'
        out.append('<section class="page">')
        out.append(f'<div class="phead"><span>错题重做纸 · 第 {i} / {len(pages)} 页</span>'
                   f'<span>{right}</span></div>')
        out.append('<div class="blocks">')
        for rec, n in page:
            h = int(CELL_MM * n) - 12
            out.append(f'<div class="block" style="height:{CELL_MM * n:.1f}mm">')
            out.append(f'<div class="qmeta">{rec["id"]} · '
                       f'{TYPE_CN.get(rec["type"], rec["type"])} · {rec["cells"]} 格</div>'
                       f'<div class="qbody"><img src="{rec["clean_image"]}" '
                       f'style="max-height:{h}mm" alt="题面"></div>')
            out.append('</div>')
        out.append('</div>')
        out.append(f'<div class="pfoot">本页含：{", ".join(ids)}'
                   + (f' · 页锚点 {anchor}' if anchor else '') + '</div>')
        out.append('</section>')
    out.append('</body></html>')
    return "".join(out)


SHEET_CSS = """
@page { size: A4; margin: 10mm; }
body.sheet { margin: 0; font-family: system-ui, "Noto Sans CJK SC", "Source Han Sans", sans-serif; color: #111; }
.page { width: 190mm; height: 276mm; display: flex; flex-direction: column; page-break-after: always; }
.page:last-child { page-break-after: auto; }
.phead { font-size: 8pt; color: #666; display: flex; justify-content: space-between;
         border-bottom: 1px solid #ccc; padding-bottom: 1mm; }
.blocks { flex: 1; display: flex; flex-direction: column; }
.block { display: flex; flex-direction: column; overflow: hidden; border-bottom: 1px dashed #bbb; }
.block:last-child { border-bottom: none; }
.qmeta { font-size: 8pt; color: #888; }
.qbody { flex: 1; padding-top: 1mm; }
.qbody img { max-width: 100%; }
.pfoot { font-size: 7pt; color: #888; border-top: 1px solid #ccc; padding-top: 1mm; }
.empty { font-size: 10pt; color: #555; }
body.gray .qbody img { filter: grayscale(1) contrast(1.1); }
"""


# ---------------------------------------------------------------- 清单页

PAGE_CSS = """
:root { --line:#d8d8d8; --bg:#fafafa; --accent:#1a5fb4; }
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui,"Noto Sans CJK SC",sans-serif; color:#111; background:var(--bg); }
header { position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); padding:10px 16px;
         display:flex; gap:16px; align-items:center; flex-wrap:wrap; z-index:5; }
header h1 { font-size:15px; margin:0 12px 0 0; }
header .muted { color:#777; font-size:12px; }
button, select { font:inherit; padding:4px 10px; border:1px solid var(--line); border-radius:5px; background:#fff; cursor:pointer; }
button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
main { padding:12px 16px 60px; max-width:1200px; }
details { background:#fff; border:1px solid var(--line); border-radius:8px; margin-bottom:10px; }
details > summary { padding:9px 12px; cursor:pointer; display:flex; gap:10px; align-items:center; flex-wrap:wrap; font-size:13px; }
.badge { font-size:11px; padding:2px 7px; border-radius:99px; background:#eee; color:#555; }
.badge.reviewed { background:#e3f2e3; color:#20621f; }
.badge.pending { background:#fdecea; color:#a3231a; }
.badge.state { background:#eef2ff; color:#1a3f8f; }
.histbox { margin:8px 0 2px; font-size:12px; }
.hist { color:#444; font-size:11px; padding:2px 0 2px 10px; border-left:2px solid var(--line); }
.hist.muted { color:#999; }
.gate { font-size:11px; color:#666; }
.gate.bad { color:#a3231a; font-weight:600; }
.gate.ok { color:#20621f; }
.body { padding:0 12px 12px; border-top:1px solid var(--line); }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:10px; }
@media (max-width:820px) { .grid { grid-template-columns:1fr; } }
.hint { font-size:11px; color:#777; margin-bottom:4px; }
.wrap { position:relative; line-height:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; cursor:crosshair; }
.wrap img { width:100%; display:block; }
.sel { position:absolute; border:1.5px solid var(--accent); background:rgba(26,95,180,.18); display:none; pointer-events:none; }
.result { width:100%; border:1px solid var(--line); border-radius:6px; background:#fff; }
.row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:10px; font-size:12px; }
.form { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px 14px; margin-top:12px; }
.field label { display:block; font-size:11px; color:#666; margin-bottom:3px; }
.field input[type=text], .field textarea { width:100%; font:inherit; padding:5px 7px; border:1px solid var(--line); border-radius:5px; }
.field textarea { min-height:56px; resize:vertical; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { font-size:12px; border:1px solid var(--line); border-radius:99px; padding:2px 9px; cursor:pointer; user-select:none; background:#fff; }
.chip.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.span2 { grid-column:1 / -1; }
pre.transcript { white-space:pre-wrap; font-size:11px; color:#444; background:#f4f4f4; padding:6px 8px; border-radius:5px; margin:0; }
"""

PAGE_JS = r"""
const S = {};   // pid -> {add:[], drop:[]}

function q(sel, root){
  const el = (root || document).querySelector(sel);
  if(!el) throw new Error('找不到元素：' + sel);      // 响声失败，不许静默退让
  return el;
}
function qa(sel, root){ return Array.from((root || document).querySelectorAll(sel)); }
function box(pid){
  const el = document.getElementById('card-' + pid);
  if(!el) throw new Error('找不到题目块 card-' + pid);
  return el;
}
function say(msg, bad){
  const b = document.getElementById('banner');
  b.style.display = 'block';
  b.style.background = bad ? '#a3231a' : '#20621f';
  b.textContent = (bad ? '✗ ' : '✓ ') + msg;
}

async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

function gates(h){
  if(!h) return '<span class="gate">客观闸门：无数据</span>';
  const bad = [];
  if(h.new_dark_px > 0) bad.push('新出现深色 ' + h.new_dark_px);
  if(h.residual_color_px > 0) bad.push('残留彩笔 ' + h.residual_color_px);
  if(h.print_holes_px > 0) bad.push('疑咬印刷体 ' + h.print_holes_px + 'px');
  const cls = bad.length ? 'bad' : 'ok';
  const txt = bad.length ? '⚠ ' + bad.join(' · ') : '✓ 闸门全过';
  return '<span class="gate ' + cls + '">' + txt + '</span>';
}

function refresh(pid, d){
  const el = box(pid);
  qa('img.under, img.result', el).forEach(img => {
    img.src = img.dataset.base + '?t=' + Date.now();   // 换 URL 才破缓存
  });
  q('.gates', el).innerHTML = gates(d.health);
  const st = d.stats || {}, h = d.health || {};
  q('.stats', el).textContent =
    '实擦 ' + st.mask_px + 'px · 彩笔 ' + st.colored_px + 'px · 模型框 ' + st.boxes
    + ' 个 · 人工加 ' + (d.manual.add || []).length + ' / 减 ' + (d.manual.drop || []).length
    + ' · 深色墨迹 ' + h.dark_before + '→' + h.dark_after;
}

// 拖拽状态放全局：松开鼠标时哪怕指针已经离开图片，也照样落笔
let DRAG = null;

function bindDrag(pid){
  const wrap = q('[data-wrap="' + pid + '"]', box(pid));
  const sel = q('.sel', wrap);
  const rel = e => { const r = wrap.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  wrap.addEventListener('mousedown', e => {
    e.preventDefault();
    DRAG = {pid: pid, wrap: wrap, sel: sel, start: rel(e), cur: null};
  });
}

window.addEventListener('mousemove', e => {
  if(!DRAG) return;
  const r = DRAG.wrap.getBoundingClientRect();
  const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
  DRAG.cur = [x, y];
  const s = DRAG.start, sel = DRAG.sel;
  const l = Math.min(s[0], x), t = Math.min(s[1], y);
  sel.style.display = 'block';
  sel.style.left = (l * 100) + '%';  sel.style.top = (t * 100) + '%';
  sel.style.width = (Math.abs(x - s[0]) * 100) + '%';
  sel.style.height = (Math.abs(y - s[1]) * 100) + '%';
});

window.addEventListener('mouseup', async () => {
  if(!DRAG) return;
  const d = DRAG; DRAG = null; d.sel.style.display = 'none';
  const s = d.start, c = d.cur || s;
  const r = [Math.min(s[0], c[0]), Math.min(s[1], c[1]),
             Math.abs(c[0] - s[0]), Math.abs(c[1] - s[1])];
  if(r[2] < 0.004 || r[3] < 0.004) return;             // 误点或没拖动
  const pid = d.pid;
  S[pid] = S[pid] || {add: [], drop: []};
  const mode = q('input[name="mode-' + pid + '"]:checked', box(pid)).value;
  (mode === 'add' ? S[pid].add : S[pid].drop).push(r.map(v => +v.toFixed(4)));
  await push(pid, mode === 'add' ? '加了一笔' : '减了一笔');
});

async function push(pid, why){
  try {
    const d = await api('/api/clean/' + pid, S[pid]);
    refresh(pid, d);
    say(pid + ' ' + (why || '') + ' · 已重算', false);
  } catch(e){
    say(pid + ' 重算失败：' + e.message, true);
  }
}

function bindButtons(pid){
  const el = box(pid);
  q('.undo', el).onclick = async () => {
    const st = S[pid] || {add: [], drop: []};
    const mode = q('input[name="mode-' + pid + '"]:checked', el).value;
    (mode === 'add' ? st.add : st.drop).pop();
    await push(pid, '撤销一笔');
  };
  q('.reset', el).onclick = async () => {
    S[pid] = {add: [], drop: []};
    await push(pid, '清空人工');
  };
  q('.locate', el).onclick = async () => {
    const btn = q('.locate', el);
    btn.disabled = true; btn.textContent = '定位中…（要问一次模型）';
    try {
      refresh(pid, await api('/api/locate/' + pid, S[pid] || {}));
      say(pid + ' 已重新定位手写', false);
    } catch(e){ say(pid + ' 定位失败：' + e.message, true); }
    finally { btn.disabled = false; btn.textContent = '重新定位手写'; }
  };
  q('.save', el).onclick = async () => {
    const body = {
      original_answer: q('.f-orig', el).value.trim() || null,
      standard_answer: q('.f-std', el).value.trim(),
      transcript: q('.f-trans', el).value,
      cells: +(q('.f-cells', el).value),
      topics: qa('.chip.on[data-kind="topic"]', el).map(c => c.dataset.value),
      error_causes: qa('.chip.on[data-kind="cause"]', el).map(c => c.dataset.value),
      status: q('.f-status', el).value,
    };
    try {
      const r = await api('/api/review/' + pid, body);
      const b = q('.badge', el);
      b.textContent = r.review === 'reviewed' ? '已审核' : '未审核';
      b.className = 'badge ' + (r.review === 'reviewed' ? 'reviewed' : 'pending');
      q('.saved', el).textContent = '已保存 ' + new Date().toLocaleTimeString();
      const w = r.warnings || [];
      if(w.length) say(pid + ' 审核已保存，但审核字段有 ' + w.length + ' 处可疑：' + w.join('；'), true);
      else say(pid + ' 审核已保存（' + b.textContent + '）', false);
    } catch(e){ say(pid + ' 审核保存失败：' + e.message, true); }
  };
}

// 界面不变量自检：这些东西少一样，界面就会静默地不更新——所以宁可当场喊出来
function selfcheck(){
  const errs = [];
  qa('details[data-pid]').forEach(d => {
    const pid = d.dataset.pid;
    if(!document.getElementById('card-' + pid)) errs.push('题目块缺少 id：card-' + pid);
    ['.gates', '.stats', '.undo', '.reset', '.locate', '.save', '.saved',
     '[data-wrap="' + pid + '"]', 'input[name="mode-' + pid + '"]', 'img.under[data-base]',
     'img.result[data-base]'].forEach(s => { if(!d.querySelector(s)) errs.push(pid + ' 缺少 ' + s); });
  });
  ['#sheet', '#goanchor', '#unrev', '#cooling', '#gray', '#banner'].forEach(s => {
    if(!document.querySelector(s)) errs.push('页头缺少 ' + s);
  });
  return errs;
}

try {
  const errs = selfcheck();
  if(errs.length){
    say('界面自检失败：' + errs.join('；'), true);
  } else {
    qa('details[data-pid]').forEach(d => {
      const pid = d.dataset.pid;
      S[pid] = JSON.parse(d.dataset.manual || '{"add":[],"drop":[]}');
      bindDrag(pid); bindButtons(pid);
    });
    qa('.chip').forEach(c => c.onclick = () => c.classList.toggle('on'));
    q('#sheet').onclick = async () => {
      const btn = q('#sheet');
      btn.disabled = true; btn.textContent = '生成中…';
      try {
        const r = await api('/api/batch', {unreviewed: q('#unrev').checked,
                                          cooling: q('#cooling').checked,
                                          gray: q('#gray').checked});
        if(r.empty) return say(r.note, true);
        const n = r.batch.pages.length;
        say('已生成打印批次 ' + r.batch.code + '（' + n + ' 页）'
            + (r.anchors.length ? ' · 页锚点 ' + r.anchors.join('、') : '')
            + ' · 已在新标签页打开，直接打印即可', n === 0);
        window.open(r.url, '_blank');
      } catch(e){ say('生成失败：' + e.message, true); }
      finally { btn.disabled = false; btn.textContent = '生成活页纸'; }
    };
    q('#goanchor').onclick = () => {
      const v = q('#anchor').value.trim();
      if(v) location.href = '/mark?anchor=' + encodeURIComponent(v);
      else say('先填页锚点，形如 3F2A-P1（印在重做纸的页脚）', true);
    };
  }
} catch(e){
  say('界面脚本出错：' + e.message, true);
}
"""


def render_list(q: dict) -> str:
    idx = build_index()
    items = idx["problems"]

    leaves, causes = topic_leaves(), all_causes()
    h = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>错题清单 · 审核与打印</title>',
         f'<style>{PAGE_CSS}</style></head><body>'
         '<div id="banner" style="display:none;padding:8px 16px;color:#fff;font:12px system-ui"></div>']
    batches = recent_batches(3)
    if batches:
        b = batches[0]
        links = " ".join(f'<a href="/mark?anchor={pg["anchor"]}">{pg["anchor"]}</a>'
                         for pg in b["pages"])
        batchbar = (f'<span class="muted">最近批次 <b>{b["code"]}</b>'
                    f'（{b["created_at"][:16].replace("T", " ")} UTC）：{links}'
                    f' · <a href="/sheet?batch={b["id"]}">看这一叠</a></span>')
    else:
        batchbar = '<span class="muted">还没有打印批次：点「生成活页纸」会创建一叠并给出页锚点</span>'
    h.append('<header><h1>错题清单 · 审核与打印</h1>'
             f'<span class="muted">索引 {idx["count"]} 题 · 生成于 {idx["built_at"]}</span>'
             '<span style="flex:1"></span>'
             f'<label class="muted"><input type="checkbox" id="unrev"> 包含未审核</label>'
             f'<label class="muted"><input type="checkbox" id="cooling"> 显示冷却中的题</label>'
             f'<label class="muted"><input type="checkbox" id="gray"> 灰度打印</label>'
             '<button class="primary" id="sheet">生成活页纸</button>'
             '<input type="text" id="anchor" placeholder="页锚点，如 3F2A-P1" style="width:140px">'
             '<button id="goanchor">标记这一页</button></header>'
             f'<div style="padding:6px 16px;border-bottom:1px solid var(--line);background:#fff">{batchbar}</div>'
             '<main>')

    for p in items:
        st = p["review"]
        badge = f'<span class="badge {"reviewed" if st == "reviewed" else "pending"}">' \
                f'{"已审核" if st == "reviewed" else "未审核"}</span>'
        chips_t = "".join(
            f'<span class="chip {"on" if t in p["topics"] else ""}" data-kind="topic" data-value="{t}">{t}</span>'
            for t in leaves)
        chips_c = "".join(
            f'<span class="chip {"on" if c in p["error_causes"] else ""}" data-kind="cause" data-value="{c}">{c}</span>'
            for c in causes)
        props = "".join(f'<span class="badge">提名：{t}</span>' for t in p["new_tag_proposals"])
        warns = p.get("warnings") or []
        warn_badge = (f'<span class="gate bad">⚠ 审核字段 {len(warns)} 处可疑</span>'
                      if warns else "")
        warn_block = ("".join(f'<div class="gate bad" style="display:block;margin:4px 0">⚠ {w}</div>'
                              for w in warns) if warns else "")
        v = S.VERDICT_CN.get(p["last_verdict"], p["last_verdict"])
        state_html = "".join(f'<span class="badge state">{t}</span>' for t in (
            p["mastery_cn"],
            f'掌握 {p["streak"]}/{S.MASTERY_STREAK}',
            f'冷却中·剩 {p["cooldown_days"]} 天' if p["cooling"] else '已脱离冷却',
            f'上次判定：{v}' if v else '还没重做过',
            f'重做 {p["attempts"]} 次'))
        hist = "".join(
            f'<div class="hist">{a["at"]} · <b>{a["verdict_cn"]}</b> · {a["channel_cn"]} · {a["source"]}'
            + (f' · 错因 {"、".join(a["error_causes"])}' if a["error_causes"] else '')
            + (f' · {a["note"]}' if a["note"] else '') + '</div>'
            for a in p["attempt_log"]) or '<div class="hist muted">还没有重做记录</div>'
        hist_box = (f'<div class="histbox"><a href="/mark?pid={p["id"]}">标记一次重做</a>'
                    f'<span class="muted">（错因属于那一次重做，所以在标记时记）</span>{hist}</div>')
        mask = p["mask_url"] or p["image"]
        orig_pa = p["original_answer"] if p["original_answer"] is not None else ""
        cells_opts = "".join(
            f'<option value="{n}" {"selected" if n == p["cells"] else ""}>{n} 格</option>'
            for n in (1, 2, 4))
        h.append(f'''<details id="card-{p['id']}" data-pid="{p['id']}" data-manual='{json.dumps(p["manual"])}'>
 <summary>{badge} <b>{p["id"]}</b> <span class="badge">{TYPE_CN.get(p["type"], p["type"])}</span>
   <span class="muted">{p["reviewed_at"] or "未审核"}</span> {props} {state_html}
   <span style="flex:1"></span> {warn_badge} <span class="gates">{gates_placeholder(p)}</span></summary>
 <div class="body">
  {warn_block}
  {hist_box}
  <div class="grid">
   <div>
     <div class="hint">原图 + 掩膜：<b>红</b>＝将擦掉，<b>黄</b>＝手写压在印刷体上。
       按住鼠标拖一个框，按下面选中的模式加/减掩膜；松手即重算。</div>
     <div class="wrap" data-wrap="{p['id']}">
       <img class="under" data-base="{mask}" src="{mask}?t=0"><div class="sel"></div>
     </div>
   </div>
   <div>
     <div class="hint">擦除结果（这一张将印上重做纸）</div>
     <img class="result" data-base="{p["clean_image"] or p["image"]}" src="{p["clean_image"] or p["image"]}?t=0">
   </div>
  </div>
  <div class="row">
   <label><input type="radio" name="mode-{p['id']}" value="add" checked> 加掩膜</label>
   <label><input type="radio" name="mode-{p['id']}" value="drop"> 减掩膜</label>
   <button class="undo">撤销上一笔</button>
   <button class="reset">清空人工</button>
   <button class="locate">重新定位手写</button>
   <span class="stats muted"></span>
   <span class="saved muted"></span>
  </div>
  <div class="form">
   <div class="field"><label>原答（学生原本的答案；看不出来就留空）</label>
     <input type="text" class="f-orig" value="{orig_pa}"></div>
   <div class="field"><label>标准答案（自动判定的唯一基准）</label>
     <input type="text" class="f-std" value="{p["standard_answer"] or ""}"></div>
   <div class="field"><label>版面格</label><select class="f-cells">{cells_opts}</select>
     <label style="margin-top:6px">审核状态</label>
     <select class="f-status">
       <option value="reviewed" {"selected" if st == "reviewed" else ""}>已审核</option>
       <option value="unreviewed" {"selected" if st != "reviewed" else ""}>未审核</option>
     </select></div>
   <div class="field span2"><label>题面转录（用于检索与打标，不印）</label>
     <textarea class="f-trans">{p["transcript"] or ""}</textarea></div>
   <div class="field span2"><label>考点（受控词表，只能选叶子）</label>
     <div class="chips">{chips_t}</div></div>
   <div class="field span2"><label>错因（属于这一次重做，不属于题目）</label>
     <div class="chips">{chips_c}</div></div>
   <div class="field span2"><button class="save primary">保存审核</button></div>
  </div>
  <div class="field span2" style="margin-top:8px"><label>AI 的原解转录 / 订正转录（人看，不印）</label>
   <pre class="transcript">{p["original_transcript"] or "（原解缺失）"}
—— 订正：{p["correction"] or "（无）"}</pre></div>
 </div>
</details>''')
    h.append(f'</main><script>{PAGE_JS}</script></body></html>')
    return "".join(h)


def gates_placeholder(p: dict) -> str:
    h = p.get("health")
    if not h:
        return '<span class="gate">客观闸门：无数据</span>'
    bad = []
    if h.get("new_dark_px"):
        bad.append(f'新出现深色 {h["new_dark_px"]}')
    if h.get("residual_color_px"):
        bad.append(f'残留彩笔 {h["residual_color_px"]}')
    if h.get("print_holes_px"):
        bad.append(f'疑咬印刷体 {h["print_holes_px"]}px')
    return (f'<span class="gate bad">⚠ {" · ".join(bad)}</span>' if bad
            else '<span class="gate ok">✓ 闸门全过</span>')


MARK_CSS = PAGE_CSS + """
.markcard { background:#fff; border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin-bottom:10px; }
.markcard.done { border-color:#20621f; border-width:2px; }
.markhead { display:flex; gap:10px; align-items:center; flex-wrap:wrap; font-size:13px; }
.markcard img.thumb { max-width:100%; max-height:140px; margin:8px 0; border:1px solid var(--line); }
.verdicts { margin-top:4px; }
.verdicts button { margin-right:6px; }
.verdicts button.ok { background:#20621f; color:#fff; border-color:#20621f; }
.verdicts button.bad { background:#a3231a; color:#fff; border-color:#a3231a; }
.verdicts button.unk { background:#8a6d00; color:#fff; border-color:#8a6d00; }
.causes { margin-top:6px; }
.note { margin-top:6px; width:min(520px,100%); padding:4px 8px; font:inherit; }
.result { margin-top:6px; font-size:12px; }
.result.bad { color:#a3231a; font-weight:600; }
.result.ok { color:#20621f; }
header a, .histbox a { color:var(--accent); }
"""

MARK_JS = r"""
const VCN = {correct: '对', wrong: '错', unreadable: '看不清'};

function q(sel, root){
  const el = (root || document).querySelector(sel);
  if(!el) throw new Error('找不到元素：' + sel);
  return el;
}
function qa(sel, root){ return Array.from((root || document).querySelectorAll(sel)); }
function card(pid){
  const el = document.getElementById('card-' + pid);
  if(!el) throw new Error('找不到题目块 card-' + pid);
  return el;
}
function say(msg, bad){
  const b = q('#banner');
  b.style.display = 'block';
  b.style.background = bad ? '#a3231a' : '#20621f';
  b.textContent = (bad ? '✗ ' : '✓ ') + msg;
}
async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body||{})});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

async function mark(pid, verdict, quiet){
  const el = card(pid);
  const res = q('.result', el);
  try {
    const r = await api('/api/attempt/' + pid, {
      verdict: verdict,
      at: q('#at').value,
      error_causes: qa('.chip.on[data-cause]', el).map(c => c.dataset.cause),
      note: (q('.note', el).value || '').trim() || null,
    });
    const e = r.effect;
    el.classList.add('done');
    res.className = 'result ' + (verdict === 'wrong' ? 'bad' : 'ok');
    res.textContent = VCN[verdict] + ' 已记录（' + e.note + '）· 掌握 '
      + r.mastery.streak + '/2 · '
      + (r.mastery.state === 'graduated' ? '毕业' : (r.cooling ? '冷却中·剩 ' + r.cooldown_days + ' 天' : '在池'));
    const sb = q('.badge.state', el);
    sb.textContent = r.mastery_cn + ' · 掌握 ' + r.mastery.streak + '/2';
    if(!quiet) {
      const w = r.warnings || [];
      say(pid + ' 记为「' + VCN[verdict] + '」· ' + e.note + (w.length ? '；注意：' + w.join('；') : ''),
          w.length > 0);
    }
    return r;
  } catch(err){
    res.className = 'result bad';
    res.textContent = '记录失败：' + err.message;
    if(!quiet) say(pid + ' 记录失败：' + err.message, true);
    return null;
  }
}

qa('.chip').forEach(c => c.onclick = () => c.classList.toggle('on'));
qa('.markcard').forEach(el => {
  const pid = el.dataset.pid;
  qa('.verdicts button', el).forEach(b => b.onclick = () => mark(pid, b.dataset.v));
});
const allok = q('#allok');
allok.onclick = async () => {
  const todo = qa('.markcard:not(.done)').map(el => el.dataset.pid);
  if(!todo.length) return say('这一页都标过了', true);
  allok.disabled = true; allok.textContent = '标对中…（0/' + todo.length + '）';
  let n = 0;
  for(const pid of todo){
    const r = await mark(pid, 'correct', true);
    if(r) n++;
    allok.textContent = '标对中…（' + (n) + '/' + todo.length + '）';
  }
  allok.disabled = false; allok.textContent = '本页全部标对';
  say('本页 ' + n + ' 题标为「对」', false);
};
"""


def render_mark(q: dict) -> str:
    """标记重做：纸上做完之后，翻回来把这一页的题连续标完。

    三个入口共用一个界面：按页锚点（纸上那张纸上的码）、按整批、按单题。
    纸上重做的判定一律由人给出（CONTEXT「判定」），所以这里没有自动判定。
    """
    idx = build_index()
    by_id = {p["id"]: p for p in idx["problems"]}
    anchor, bid, pid = first(q, "anchor"), first(q, "batch"), first(q, "pid")
    batch, page_no, ids = None, None, []
    if anchor:
        batch, page_no = resolve_anchor(anchor)
        if batch:
            ids = batch_ids(batch, page_no)
    elif bid:
        batch = load_batch(bid)
        if batch:
            ids = batch_ids(batch)
    elif pid:
        ids = [pid]

    h = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<title>标记重做</title>',
         f'<style>{MARK_CSS}</style></head><body>',
         '<div id="banner" style="display:none;padding:8px 16px;color:#fff;font:12px system-ui"></div>']

    if not ids or not any(i in by_id for i in ids):
        h.append('<header><h1>标记重做</h1><a href="/">回清单</a></header><main>')
        h.append('<p>没找到要标记的题。可以：</p><ul>'
                 '<li>从清单页点某道题的「标记一次重做」</li>'
                 '<li>输入重做纸页脚的页锚点（形如 <code>3F2A-P1</code>）</li></ul>')
        bs = recent_batches(8)
        if bs:
            h.append('<h3>最近的打印批次</h3><ul>')
            for b in bs:
                links = " ".join(f'<a href="/mark?anchor={pg["anchor"]}">{pg["anchor"]}</a>'
                                 for pg in b["pages"])
                h.append(f'<li><b>{b["code"]}</b>（{b["created_at"][:16].replace("T", " ")} UTC）'
                         f' · {len(b["pages"])} 页 · {links}'
                         f' · <a href="/sheet?batch={b["id"]}">看这一叠</a></li>')
            h.append('</ul>')
        h.append('</main></body></html>')
        return "".join(h)

    if batch:
        head = (f'批次 <b>{batch["code"]}</b>'
                f'（{batch["created_at"][:16].replace("T", " ")} UTC）')
        head += f' · 第 {page_no} 页' if page_no else f' · 整批 {len(batch["pages"])} 页'
        head += f' · <a href="/sheet?batch={batch["id"]}">看这一叠</a>'
    else:
        head = f'单题 <b>{pid}</b>'
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    h.append(f'<header><h1>标记重做</h1><span class="muted">{head}</span>'
             f'<span style="flex:1"></span>'
             f'<label class="muted">做这套题的日期 <input type="date" id="at" value="{today}"></label>'
             f'<button class="primary" id="allok">本页全部标对</button>'
             f'<a href="/">回清单</a></header><main>')
    h.append('<p class="muted">判定由你给出（纸上重做不拍照、不自动判）。判错就顺手记一下错因——'
             '错因属于<strong>那一次重做</strong>，不属于题目，漏了就只剩一个「错」字。</p>')

    causes = all_causes()
    for i in ids:
        p = by_id.get(i)
        if not p:
            continue
        chips = "".join(f'<span class="chip" data-cause="{c}">{c}</span>' for c in causes)
        hist = "".join(
            f'<div class="hist">{a["at"]} · <b>{a["verdict_cn"]}</b> · {a["channel_cn"]}'
            + (f' · 错因 {"、".join(a["error_causes"])}' if a["error_causes"] else '')
            + (f' · {a["note"]}' if a["note"] else '') + '</div>'
            for a in p["attempt_log"]) or '<div class="hist muted">还没有重做记录</div>'
        h.append(f'''<section class="markcard" id="card-{p['id']}" data-pid="{p['id']}">
 <div class="markhead"><b>{p['id']}</b>
   <span class="badge">{TYPE_CN.get(p['type'], p['type'])}</span>
   <span class="badge state">{p['mastery_cn']} · 掌握 {p['streak']}/{S.MASTERY_STREAK}</span>
   <span class="badge">{f'冷却中·剩 {p["cooldown_days"]} 天' if p['cooling'] else '已脱离冷却'}</span>
   <span class="muted">{'、'.join(p['topics']) or '（还没打考点）'}</span></div>
 <div><img class="thumb" src="{p['clean_image']}" alt="题面"></div>
 <div class="verdicts">
   <button class="ok" data-v="correct">对</button>
   <button class="bad" data-v="wrong">错</button>
   <button class="unk" data-v="unreadable">看不清</button></div>
 <div class="causes">{chips}</div>
 <input class="note" placeholder="备注（可选，例如「卡在第二问」）">
 <div class="result muted">还没标</div>
 <div class="histbox">{hist}</div>
</section>''')
    h.append(f'</main><script>{MARK_JS}</script></body></html>')
    return "".join(h)


ANCHOR_BASE = "http://127.0.0.1:8765"


def selftest() -> int:
    """不靠浏览器也能跑的界面不变量检查。

    这个自检是被一个真 bug 逼出来的：`<details>` 少了 id，`getElementById` 返回 null，
    于是查询退化到整篇文档、第二题的按钮绑到了第一题的按钮上、读数写进错的地方——
    而界面上**什么都不报错，只是不再更新**。凡是"静默不更新"这一类，都要有响声。
    """
    html = render_list({})
    errs = []
    pids = re.findall(r'data-pid="([^"]+)"', html)
    if not pids:
        errs.append("页面里没有任何题目块（没有题可显示？）")
    for pid in pids:
        if f'id="card-{pid}"' not in html:
            errs.append(f'{pid}: <details> 缺少 id="card-{pid}"')
            continue
        blk = html.split(f'id="card-{pid}"', 1)[-1].split("</details>", 1)[0]
        # 查的是 HTML 里的实际写法，不是 CSS 选择器写法——这两种混过一次，全是误报
        for s in ('class="gates"', 'class="stats', 'class="undo"', 'class="reset"',
                  'class="locate"', 'class="save', 'class="saved',
                  f'data-wrap="{pid}"', f'name="mode-{pid}"',
                  'class="under"', 'class="result"', "data-base="):
            if s not in blk:
                errs.append(f"{pid}: 块内缺少 {s}")
    for s in ('id="sheet"', 'id="goanchor"', 'id="unrev"', 'id="cooling"',
              'id="gray"', 'id="banner"', 'id="anchor"'):
        if s not in html:
            errs.append(f"页头缺少 {s}")

    # 标记页：单题入口（三个入口共用同一个界面）
    if pids:
        mk = render_mark({"pid": [pids[0]]})
        for s in (f'id="card-{pids[0]}"', 'class="markcard"', 'data-v="correct"',
                  'data-v="wrong"', 'data-v="unreadable"', 'data-cause=', 'id="at"',
                  'id="allok"', 'id="banner"', 'class="result', 'class="badge state"'):
            if s not in mk:
                errs.append(f"标记页缺少 {s}")
        if "function mark(" not in MARK_JS or "/api/attempt/" not in MARK_JS:
            errs.append("标记页脚本缺少回写调用")

    # 页锚点必须"指不回来就明确失败"，绝不能悄悄落到别的题上
    b, _ = resolve_anchor("ZZZZ-P9")
    if b is not None:
        errs.append("不存在的页锚点竟然解析出了批次")
    for s in ("这是预览", "没有页锚点"):
        if s not in render_sheet({}):
            errs.append(f"预览活页纸没有说明它没有页锚点（缺「{s}」）")
    js = PAGE_JS
    for fn in ("function selfcheck", "function box(", "function push("):
        if fn not in js:
            errs.append(f"脚本缺少 {fn}")
    if errs:
        print("界面自检未通过：")
        for e in errs:
            print("  ✗", e)
        return 1
    print(f"界面自检通过：{len(pids)} 个题目块，块内该有的元素都在，页头控件齐全。")
    return 0


# ---------------------------------------------------------------- 落盘数据体检

def all_cards() -> dict:
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(PROBLEMS.glob("*.json"))}


def audit() -> int:
    """落盘数据体检：只读磁盘、可随时重跑、什么都不改。

    它存在的理由是一次真事故：验收是对 `runs/` 里那次调用打的分，而题卡在验收之后
    被界面改坏了，没人发现——日志说这张图的转录完整，盘上的卡却写着另一道题的题干。
    所以检查必须跑在**落盘的数据**上，而且必须能随时重跑，而不是只在写卡那一刻跑一次。
    """
    errs, notes = [], []
    cards = all_cards()

    # 1. 卡片自检：复用界面那一份规则，规则只留一份
    for pid, rec in cards.items():
        for w in card_warnings(rec):
            # 「解答题的标准答案为空」是按设计如此（解答题不机判），不是毛病。
            # 把按设计如此的事报成问题，会训练人忽略体检——那比漏报更糟。
            if w.startswith("标准答案为空") and rec["problem"]["type"] == "solution":
                notes.append(f"{pid}：{w}（解答题按设计不机判，属正常）")
            else:
                errs.append(f"{pid}：{w}")

    # 2. 资产指向的文件是否都还在
    for pid, rec in cards.items():
        for key, label in (("image", "题面裁剪图"), ("clean_image", "擦除后的题面图")):
            rel = rec["problem"].get(key)
            if rel and not (S.ROOT / rel).is_file():
                errs.append(f"{pid}：{label}指向的文件不存在（{rel}）")
        page = (rec.get("source") or {}).get("page_image")
        if page and not (S.ROOT / page).is_file():
            errs.append(f"{pid}：整页原图不见了（{page}）——它是最后的底，不该删")

    # 3. 题干转录逐字相同：这次事故的指纹。
    #    两道不同的题不可能有同一段题干。重复只有两种可能：同一道题录了两遍，
    #    或者一张卡的文字被写进了另一张（这次是后者）。
    by_text = {}
    for pid, rec in cards.items():
        t = (rec["problem"].get("transcript") or "").strip()
        if t:
            by_text.setdefault(t, []).append(pid)
    for t, pids in by_text.items():
        if len(pids) > 1:
            errs.append("题干转录逐字相同：" + "、".join(pids)
                        + f"（{t[:36]}…）——不是同一道题录了两遍，"
                          "就是一张卡的文字被写进了另一张")

    # 4. 索引是派生物，不一致就重建
    if INDEX_PATH.is_file():
        idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        listed = [p["id"] for p in idx.get("problems", [])]
        for pid in sorted(set(cards) - set(listed)):
            errs.append(f"{pid}：在盘上，不在索引里（跑 --rebuild-index）")
        for pid in sorted(set(listed) - set(cards)):
            errs.append(f"{pid}：在索引里，卡片已不在")
        if idx.get("count") != len(listed):
            errs.append(f"索引自称 {idx.get('count')} 题，实际列了 {len(listed)} 题")
    else:
        notes.append("还没有索引（data/index.json）——跑 --rebuild-index 生成")

    # 5. 打印批次引用的题是否还在：纸已经印出去了，指不回来就是废纸
    batches = sorted(BATCHES.glob("*.json")) if BATCHES.is_dir() else []
    for bp in batches:
        try:
            b = json.loads(bp.read_text(encoding="utf-8"))
        except Exception as e:
            errs.append(f"批次 {bp.name} 读不动（{e}）")
            continue
        code = b.get("code", bp.stem)
        for pg in b.get("pages", []):
            ids = pg.get("ids") or []
            cells = pg.get("cells") or []
            for pid in ids:
                if pid not in cards:
                    errs.append(f"批次 {code} 第 {pg.get('no')} 页引用了不存在的题卡：{pid}")
            if len(cells) != len(ids):
                errs.append(f"批次 {code} 第 {pg.get('no')} 页：{len(ids)} 道题却记了 {len(cells)} 个版面格")
            elif sum(cells) > CELLS_PER_PAGE:
                errs.append(f"批次 {code} 第 {pg.get('no')} 页：版面格合计 {sum(cells)}，超过一页 {CELLS_PER_PAGE} 格")

    # 6. 掌握状态与重做历史是否自洽
    for pid, rec in cards.items():
        m, n = rec.get("mastery") or {}, len(rec.get("attempts") or [])
        streak = m.get("streak") or 0
        if streak and n == 0:
            errs.append(f"{pid}：连对 {streak} 次却没有任何重做记录")
        if m.get("state") == "graduated" and streak < S.MASTERY_STREAK:
            errs.append(f"{pid}：已毕业，但连对只有 {streak} 次（毕业要 {S.MASTERY_STREAK} 次）")

    print(f"落盘数据体检：{len(cards)} 张题卡、{len(batches)} 个打印批次")
    for n in notes:
        print(f"  · {n}")
    if errs:
        print(f"\n查出 {len(errs)} 处问题：")
        for e in errs:
            print(f"  ✗ {e}")
        print("\n体检只报告、不修改。改完再跑一遍——可重跑正是它存在的意义。")
        return 1
    print("  ✓ 没有查出问题")
    return 0


SUSPECT_FIELDS = [("problem", "transcript", "题干转录"),
                  ("standard_answer", "value", "标准答案"),
                  ("original_solution", "original_answer", "原答")]


def repair_card(pid: str, apply: bool) -> int:
    """让模型对着整页原图重抽一次，只修「像是来自另一道题」的那几个字段。

    为什么要重抽而不是手改：被污染的字段没有可信来源，必须回到图上取。
    为什么限定字段：卡上其余部分（擦除掩膜、版面格、重做历史、考点错因）是人动过的，
    整卡重跑会把它全冲掉——那是以「修数据」为名的第二次破坏。
    """
    rec = load_card(pid)
    page_rel = (rec.get("source") or {}).get("page_image")
    if not page_rel or not (S.ROOT / page_rel).is_file():
        print(f"✗ {pid} 的整页原图不可用（{page_rel!r}），修不了")
        return 2

    causes, topics = S.load_vocab()
    user_text = (f"考点词表（只能从中选）：{json.dumps(topics, ensure_ascii=False)}\n"
                 f"错因词表（只能从中选）：{json.dumps(causes, ensure_ascii=False)}\n"
                 f"\n输出必须是这个形状的 JSON：\n{S.EXTRACT_JSON_SHAPE}")
    cfg = S.role_config("extract")
    print(f"→ 对着 {page_rel} 重抽（{cfg['provider']} / {cfg['model']}）…")
    text, _ = S.chat(
        [{"role": "system", "content": S.EXTRACT_SYSTEM},
         {"role": "user", "content": [
             {"type": "text", "text": user_text},
             {"type": "image_url", "image_url": {"url": S.image_to_data_url(S.ROOT / page_rel)}}]}],
        cfg["role"], tag=f"repair-{pid}")
    got = S.validate_extraction(S.extract_json(text), causes, topics)
    fresh = {"problem": {"transcript": got["problem_transcript"]},
             "standard_answer": {"value": got["standard_answer"]},
             "original_solution": {"original_answer": got["original_answer"]}}

    print(f"\n先看它认的是不是同一道题：")
    print(f"  题型  图上 {got['problem_type']}   卡上 {rec['problem']['type']}")
    print(f"  裁框  图上 {got['bbox']}   卡上 {(rec.get('source') or {}).get('bbox_norm')}")

    diffs = []
    print("\n三个可疑字段（＝相同，≠不同）：")
    for sec, key, label in SUSPECT_FIELDS:
        before = (rec.get(sec) or {}).get(key)
        after = fresh[sec][key]
        print(f"  {'≠' if before != after else '＝'} {label}")
        print(f"      卡上：{before!r}")
        print(f"      图上：{after!r}")
        if before != after:
            diffs.append((sec, key, before, after))
    if not diffs:
        print("\n没有差异，不用改。")
        return 0
    if not apply:
        print(f"\n（预览）要改上面 {len(diffs)} 个字段。加 --apply 才写盘。")
        return 0

    with LOCK:
        rec = load_card(pid)
        for sec, key, before, after in diffs:
            rec.setdefault(sec, {})[key] = after
        # 被改的正是「审核」覆盖的字段，所以这道题退回未审核：它需要你再看一眼。
        if (rec.get("review") or {}).get("status") == "reviewed":
            rec["review"] = {"status": "unreviewed", "reviewed_at": None,
                             "reopened_because": "自动修复动了题干/标准答案/原答，需重新确认"}
        rec.setdefault("provenance", {}).setdefault("repairs", []).append({
            "at": S.now_iso(), "provider": cfg["provider"], "model": cfg["model"],
            "page_image": page_rel,
            "fields": [f"{sec}.{key}" for sec, key, _, _ in diffs],
            "before": {f"{sec}.{key}": b for sec, key, b, _ in diffs},
            "after": {f"{sec}.{key}": a for sec, key, _, a in diffs},
        })
        save_card(pid, rec)
        build_index()
    print(f"\n已写回 {pid}，并退回未审核（改动的是审核覆盖的字段）。索引已重建。")
    print("  跑 python3 proto/server.py --audit 复核。")
    return 0


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "cuotiben/0.1"

    def log_message(self, fmt, *a):
        print(f"  {self.address_string()} {fmt % a}")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def html(self, s: str):
        self._send(200, s.encode("utf-8"), "text/html; charset=utf-8")

    def js(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def err(self, code: int, msg: str):
        self._send(code, msg.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = urllib.parse.parse_qs(query)
        if path == "/":
            return self.html(render_list(q))
        if path == "/sheet":
            return self.html(render_sheet(q))
        if path == "/mark":
            return self.html(render_mark(q))
        if path == "/api/index":
            return self.js(build_index())
        if path.startswith("/assets/"):
            name = urllib.parse.unquote(path[len("/assets/"):])
            if not re.fullmatch(r"[\w.\-]+", name):
                return self.err(400, "bad name")
            f = ASSETS / name
            if not f.is_file():
                return self.err(404, "not found")
            ctype = "image/png" if f.suffix == ".png" else "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype)
        return self.err(404, "not found")

    def do_POST(self):
        path, _, _ = self.path.partition("?")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        try:
            if path.startswith("/api/clean/"):
                return self.js(self.do_clean(path.rsplit("/", 1)[1], body))
            if path.startswith("/api/locate/"):
                return self.js(self.do_locate(path.rsplit("/", 1)[1], body))
            if path.startswith("/api/review/"):
                return self.js(self.do_review(path.rsplit("/", 1)[1], body))
            if path == "/api/batch":
                return self.js(self.do_batch(body))
            if path.startswith("/api/attempt/"):
                return self.js(self.do_attempt(path.rsplit("/", 1)[1], body))
        except ValueError as e:
            return self.err(400, str(e))
        except Exception as e:
            return self.err(500, f"{e.__class__.__name__}: {e}")
        return self.err(404, "not found")

    # --- 写入动作（全部经过这里，ADR 0001）

    def do_batch(self, body: dict) -> dict:
        """生成一叠重做纸 = 落一个打印批次。页锚点从此稳定，纸上那张纸永远指得回来。"""
        with LOCK:
            idx = build_index()
            rec = new_batch(idx, {k: v for k, v in (body or {}).items() if v})
        if not rec:
            return {"ok": True, "empty": True, "batch": None,
                    "note": "默认清单里没有题：只有未毕业、且已脱离冷却（距上次重做满 7 天）的题"
                            "才默认要印。新录入的题都在冷却里——勾上「显示冷却中的题」就能先打。"}
        return {"ok": True, "empty": False, "batch": rec, "url": f"/sheet?batch={rec['id']}",
                "anchors": [p["anchor"] for p in rec["pages"]]}

    def do_attempt(self, pid: str, body: dict) -> dict:
        """判定回写：纸上重做一律由人给出判定，所以这里没有模型。"""
        with LOCK:
            rec = load_card(pid)
            verdict = body.get("verdict")
            if verdict not in S.VERDICT_CN:
                raise ValueError(f"判定取值非法：{verdict!r}"
                                 f"（只能是 {' / '.join(S.VERDICT_CN)}）")
            at_raw = (body.get("at") or "").strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", at_raw):
                # 只给日期时按本机时区的当天中午算：卡里的时刻是 +08:00，
                # 直接用 UTC 零点会与之错位成"负天数"
                local = datetime.now().astimezone().tzinfo
                at_raw = datetime.strptime(at_raw, "%Y-%m-%d").replace(hour=12, tzinfo=local).isoformat()
            if at_raw:
                at_dt = S._as_dt(at_raw)
                # 未来日期是手填字段最容易犯的错，而它会凭空制造一段冷却
                if at_dt and at_dt > datetime.now(timezone.utc) + timedelta(days=1):
                    raise ValueError(f"重做日期在未来（{at_raw[:10]}）——冷却会凭空多出一段")
            eff = S.apply_attempt(rec, verdict, source=body.get("source") or "人工确认",
                                  error_causes=body.get("error_causes"),
                                  at=at_raw or None, channel=body.get("channel") or "paper",
                                  note=body.get("note"))
            warns = []
            if verdict == "wrong" and not (body.get("error_causes") or []):
                warns.append("判错但没记错因——错因属于那一次重做，漏了就只剩一个「错」字")
            if rec["review"]["status"] != "reviewed":
                warns.append("这道题还没审核：它的标准答案还不可信")
            save_card(pid, rec)
            build_index()
        return {"ok": True, "effect": eff, "mastery": rec["mastery"],
                "mastery_cn": MASTERY_CN.get(rec["mastery"].get("state"),
                                             rec["mastery"].get("state")),
                "cooling": S.is_cooling(rec), "cooldown_days": S.cooldown_days_left(rec),
                "warnings": warns}

    def do_clean(self, pid: str, body: dict) -> dict:
        with LOCK:
            rec = load_card(pid)
            old = rec["problem"].get("clean") or {}
            img = Image.open(S.ROOT / rec["problem"]["image"]).convert("RGB")
            manual = {"add": body.get("add") or [], "drop": body.get("drop") or []}
            out, vis, stats = S.apply_clean(img, auto_norm=old.get("boxes_norm"), manual=manual)
            health = S.clean_health(img, out)
            ASSETS.mkdir(parents=True, exist_ok=True)
            out.save(ASSETS / f"{pid}-clean.png")
            vis.save(ASSETS / f"{pid}-cleanmask.png")
            rec["problem"]["clean_image"] = str((ASSETS / f"{pid}-clean.png").relative_to(S.ROOT))
            keep = {k: old.get(k) for k in ("boxes_norm", "verify") if k in old}
            rec["problem"]["clean"] = {**keep, "method": "erase_ink", "manual": manual,
                                       "health": health, **stats}
            save_card(pid, rec)
            build_index()
        return {"mask_url": f"/assets/{pid}-cleanmask.png",
                "clean_url": f"/assets/{pid}-clean.png",
                "stats": stats, "health": health, "manual": manual}

    def do_locate(self, pid: str, body: dict) -> dict:
        """重新问一次模型：手写在哪。慢，所以是显式按钮，不在每次编辑时跑。"""
        rec = load_card(pid)
        cfg = S.role_config("extract")
        text, _ = S.chat(
            [{"role": "system", "content": S.HANDWRITING_SYSTEM},
             {"role": "user", "content": [
                 {"type": "text", "text": "标出这张图里所有手写字迹的位置。"},
                 {"type": "image_url",
                  "image_url": {"url": S.image_to_data_url(S.ROOT / rec["problem"]["image"])}}]}],
            cfg["role"], tag=f"locate-{pid}")
        boxes = []
        try:
            got = S.extract_json(text)
        except Exception:
            got = {}
        for b in (got.get("handwriting") or []):
            bb = b.get("bbox_norm")
            if isinstance(bb, list) and len(bb) == 4 and all(isinstance(v, (int, float)) for v in bb):
                x, y, w, h = [min(1.0, max(0.0, float(v))) for v in bb]
                if w > 0.005 and h > 0.005:
                    boxes.append([x, y, w, h])
        with LOCK:
            rec = load_card(pid)
            old = rec["problem"].get("clean") or {}
            img = Image.open(S.ROOT / rec["problem"]["image"]).convert("RGB")
            manual = {"add": body.get("add") or [], "drop": body.get("drop") or []}
            out, vis, stats = S.apply_clean(img, auto_norm=boxes, manual=manual)
            health = S.clean_health(img, out)
            out.save(ASSETS / f"{pid}-clean.png")
            vis.save(ASSETS / f"{pid}-cleanmask.png")
            rec["problem"]["clean_image"] = str((ASSETS / f"{pid}-clean.png").relative_to(S.ROOT))
            rec["problem"]["clean"] = {**{k: old.get(k) for k in ("verify",) if k in old},
                                       "method": "erase_ink", "boxes_norm": boxes,
                                       "manual": manual, "health": health, **stats}
            save_card(pid, rec)
            build_index()
        return {"mask_url": f"/assets/{pid}-cleanmask.png",
                "clean_url": f"/assets/{pid}-clean.png",
                "stats": stats, "health": health, "manual": manual, "boxes_norm": boxes}

    def do_review(self, pid: str, body: dict) -> dict:
        with LOCK:
            rec = load_card(pid)
            if "original_answer" in body:
                rec["original_solution"]["original_answer"] = body["original_answer"] or None
            if "standard_answer" in body:
                rec.setdefault("standard_answer", {})
                rec["standard_answer"]["value"] = body["standard_answer"]
            if "transcript" in body:
                rec["problem"]["transcript"] = body["transcript"]
            if "topics" in body:
                rec["topics"] = body["topics"]
            if "error_causes" in body:
                rec["error_causes"] = body["error_causes"]
            if body.get("cells"):
                rec["print"] = {"cells": int(body["cells"]),
                                "cells_source": "manual"}
            status = body.get("status") or "reviewed"
            rec["review"] = {"status": status,
                             "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
                             if status == "reviewed" else None}
            warns = card_warnings(rec)
            save_card(pid, rec)
            build_index()
        return {"ok": True, "review": rec["review"]["status"], "warnings": warns}


def main() -> int:
    ap = argparse.ArgumentParser(description="写入服务：审核 + 掩膜编辑 + 活页纸排版")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--selftest", action="store_true",
                    help="只跑界面不变量自检（不开服务），改了界面先跑这个")
    ap.add_argument("--audit", action="store_true",
                    help="落盘数据体检（只读、可重跑）：卡片自检、资产、索引、批次、掌握状态")
    ap.add_argument("--rebuild-index", action="store_true", help="只重建派生索引")
    ap.add_argument("--repair", metavar="PID",
                    help="对着整页原图重抽，只修「像是来自另一道题」的字段")
    ap.add_argument("--apply", action="store_true", help="配合 --repair：真的写盘")
    args = ap.parse_args()
    # 写入服务自己也会调模型（重新定位手写、--repair），所以它也得加载密钥。
    # 这里少一行，界面上那个按钮就是死的：密钥在 .env.local / ~/.env.local 里，
    # 而 load_env() 过去只在 slice.py 的入口里被调用。
    S.load_env()
    if args.selftest:
        return selftest()
    if args.audit:
        return audit()
    if args.rebuild_index:
        print(f"索引已重建：{build_index()['count']} 题")
        return 0
    if args.repair:
        return repair_card(args.repair, args.apply)
    global ANCHOR_BASE
    ANCHOR_BASE = f"http://{args.host}:{args.port}"
    idx = build_index()
    print(f"写入服务已启动：http://{args.host}:{args.port}/")
    print(f"  清单页（审核 + 掩膜编辑）：http://{args.host}:{args.port}/")
    print(f"  活页纸（可打印）：http://{args.host}:{args.port}/sheet")
    print(f"  派生索引：{INDEX_PATH.relative_to(S.ROOT)}（{idx['count']} 题，每次写入后重建）")
    print("  提示：未审核的题不进重做纸；原型阶段可在清单页勾「包含未审核」试打。")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
