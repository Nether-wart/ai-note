#!/usr/bin/env python3
"""错题本 · 纵向切片原型（只验最危险的一段）

验证三件事：
  1. 抽取 —— 从一张真实的手写错题照片里，拿到 题面 / 原解 / 正解 / 标准答案 / 标签
  2. 切图 —— 按模型给出的归一化 bbox 裁出一张可打印的题面图
  3. 判定 —— 标准答案 与 第二次手写答案 的等价性（外加一组纯文本等价性用例）

刻意不做：不碰 PaddleOCR、不碰 Docusaurus、不碰数据库、不做界面。

产出直接落成「一题一文件」（见 docs/adr/0001），因为它同时是领域模型的第一版草稿：
`review.status` 对应「未审核」，`attempts[]` 对应每次重做，`mastery` 对应掌握与毕业。

用法：
  python3 proto/slice.py models
  python3 proto/slice.py extract samples/某一页.jpg --note "照片里是第 12 题"
  python3 proto/slice.py judge  data/problems/<id>.json samples/重做后的那一页.jpg
  python3 proto/slice.py equiv  data/problems/<id>.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROBLEMS = DATA / "problems"
ASSETS = DATA / "assets"
PAGES = DATA / "pages"
VOCAB = DATA / "vocab"
RUNS = ROOT / "runs"

# ---------------------------------------------------------------- 模型角色与 provider

# 单用户（ADR 0003）：候选池由你自己维护，但只允许境内已核实的 endpoint——
# 防的不是别人，是自己手滑：一张手写照片包含的信息一旦出境就收不回来。
PROVIDERS = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-vl-max",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-v4-flash-vision-exp",
    },
}

# 角色 → 默认 provider 与模型。可用环境变量覆盖：EXTRACT_PROVIDER / EXTRACT_MODEL / JUDGE_*
# judge 角色不做视觉（只做文本等价比对），所以用正式版即可；
# extract 角色要读手写，暂用实验视觉模型，等真实照片的验收结果说话。
ROLE_DEFAULTS = {
    "extract": {"provider": "deepseek", "model": "deepseek-flash"},
    "judge": {"provider": "deepseek", "model": "deepseek-flash"},
}


def role_config(role: str) -> dict:
    """把一个模型角色解析成 {provider, base_url, model, key_env}。"""
    if role not in ROLE_DEFAULTS:
        die(f"未知的模型角色：{role!r}（只有 extract 与 judge）")
    prefix = role.upper()
    default = ROLE_DEFAULTS[role]
    provider = os.environ.get(f"{prefix}_PROVIDER", default["provider"]).strip()
    if provider not in PROVIDERS:
        die(
            f"{prefix}_PROVIDER 指向的 provider 不在白名单里：{provider!r}\n"
            f"  可选：{', '.join(PROVIDERS)}"
        )
    preset = PROVIDERS[provider]
    # 只换了 provider 而没指定模型时，落到那家 provider 的预设默认，而不是角色原来的模型
    fallback = default["model"] if provider == default["provider"] else preset["default_model"]
    model = os.environ.get(f"{prefix}_MODEL", "").strip() or fallback
    return {
        "role": role,
        "provider": provider,
        "base_url": preset["base_url"],
        "model": model,
        "key_env": preset["key_env"],
    }


MAX_SIDE = 1600          # 送进模型的图片最长边（控制延迟与花费）
JPEG_QUALITY = 88
TIMEOUT = 180
COOLDOWN_DAYS = 7        # 冷却期：距上次重做不满 7 天，这次重做不推进掌握
MASTERY_STREAK = 2       # 掌握 = 连续 2 次被判对，且两次都已脱离冷却


# ---------------------------------------------------------------- 基础设施

def die(msg: str, code: int = 2):
    print(f"\n✗ {msg}\n", file=sys.stderr)
    sys.exit(code)


def info(msg: str):
    print(msg, flush=True)


def load_env():
    """密钥来源：仓库根的 .env.local 优先，其次 ~/.env.local。

    放在家目录也是合理做法（密钥物理上不在仓库里），所以两种都认；
    只打印实际用的是哪一个，绝不打印值。已存在的环境变量不覆盖。
    """
    for f in (ROOT / ".env.local", Path.home() / ".env.local"):
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        print(f"· 密钥文件：{f}")


def require_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        die(
            f"没有找到 {env_name}。\n"
            "  请在仓库根目录建一个 .env.local（已在 .gitignore 里），写入：\n"
            f"      {env_name}=xxxx\n"
            "  不要把 key 贴进对话或提交进仓库。"
        )
    return key


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def save_run(tag: str, payload: dict, response: dict):
    """把每次调用留档，便于回看提示词效果与花销。图片数据不入档。"""
    RUNS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    safe = re.sub(r"[^\w.-]+", "-", tag)[:40]
    path = RUNS / f"{stamp}-{safe}.json"
    slim = json.loads(json.dumps(payload))
    for m in slim.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    part["image_url"] = {"url": "<image omitted>"}
    path.write_text(
        json.dumps({"payload": slim, "response": response}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def image_to_data_url(path: Path) -> str:
    try:
        img = Image.open(path)
    except Exception as e:
        die(f"读不了图片 {path}：{e}\n（HEIC 之类的格式请先转成 JPG/PNG）")
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def chat(messages: list, role: str, tag: str, temperature: float = 0.0) -> tuple[str, dict]:
    cfg = role_config(role)
    key = require_key(cfg["key_env"])
    payload = {"model": cfg["model"], "messages": messages, "temperature": temperature}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for attempt in range(3):
        try:
            r = requests.post(
                f'{cfg["base_url"]}/chat/completions', headers=headers, json=payload,
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code >= 400:
            die(f"HTTP {r.status_code}（{tag}）：{r.text[:500]}")
        data = r.json()
        save_run(tag, payload, data)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):  # 少数实现会返回分片
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content, data.get("usage", {}) or {}
    die(f"调用失败（{tag}），重试 3 次：{last}")


def extract_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S).strip()
    start = t.find("{")
    if start < 0:
        raise ValueError(f"响应里没有 JSON：{text[:300]}")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start : i + 1])
    raise ValueError(f"JSON 不完整：{text[:300]}")


def load_vocab() -> tuple[list[str], list[str]]:
    causes = json.loads((VOCAB / "error-causes.json").read_text(encoding="utf-8"))["错因"]
    outline = json.loads((VOCAB / "topic-outline.seed.json").read_text(encoding="utf-8"))
    leaves = [
        f'{node["id"]}/{child["id"]}'
        for node in outline["nodes"]
        for child in node.get("children", [])
    ]
    return causes, leaves


# ---------------------------------------------------------------- 提示词

EXTRACT_SYSTEM = """你在为一个错题本做录入。你会收到一张错题照片：上面可能同时有印刷体的题干、手写的解答（可能是错的），以及后来补上的订正或批注（常常是红笔）。

把其中**一道题**整理成结构化数据。铁律：

1. 只输出一个 JSON 对象。不要解释，不要 markdown 代码块。
2. 看不清就不要猜：不确定的字段填 null，并压低对应的置信度。编造内容比留空有害得多。
3. original_solution 只转录照片上真实手写着的内容，不要替它补全，不要替它改正。
4. **原答与订正必须分开记。**
   - original_answer 是学生**自己原本**给出的最终答案（选项字母或最终结果），不含任何订正；
   - correction_transcript 是后来补上的订正或批注（红笔、老师批改、后补的正解）的逐字转录，没有就填 null。
   看到订正里的正答，**不等于**学生答对了。若分不清哪个是原答、哪个是订正，original_answer 填 null、
   压低置信度，并在 notes 里说明。把订正误认成原答会制造出「学生答对了」的假象——这是最难被发现的错，
   因为它会让一道真错题悄悄溜出错题本。
5. correct_solution 必须是你自己解出来的完整正确解答：照片上通常没有正解，这是正解唯一的来源。
   照片上的订正即使完全正确，也只能进 correction_transcript，不能当作你解答的来源。
6. standard_answer 是供机器比对的最终答案（不是解题过程）：
   - 题型为 choice 时填选项字母，例如 "B"；
   - 题型为 fillin 时填最终结果的简洁数学写法，例如 "x=2√3/3"；
   - 题型为 solution（解答题/证明题）时填 null——这类题按设计走人工确认。
7. problem_transcript 是**要直接印在重做纸上的文字**，所以数学一律写成 LaTeX（行内 $...$），
   不要用 x² 这类纯文本上标。一个符号转错就等于换了一道题，而做题的人看不出这一点。
8. 题型为 choice 时，把选项拆进 options 数组；problem_transcript 里只写题干，不要重复选项内容。
9. topics 与 error_causes 只能从下面给定的词表中挑选，不得自造。
   找不到合适的就把你想造的词写进 new_tag_proposals（这只是提案，不会直接入库）。
10. problem_bbox_norm 用归一化坐标 [x, y, w, h]，取值 0~1，原点在左上角，
    只框住题干本身（不含手写解答，也不含订正）。"""

EXTRACT_JSON_SHAPE = """{
  "problem_bbox_norm": [0.08, 0.12, 0.84, 0.20],
  "problem_type": "choice | fillin | solution",
  "problem_transcript": "题干的文字内容，数学用 LaTeX；choice 题只写题干",
  "options": [{"label": "A", "text": "$a\\\\le -5$"}],
  "original_solution_present": true,
  "original_answer": "学生自己原本给出的最终答案或选项字母；分不清原答与订正就填 null",
  "original_solution_transcript": "照片上手写解答的逐字转录；没有就填 null",
  "correction_transcript": "后来补上的订正/批注的逐字转录；没有就填 null",
  "correct_solution": "完整的正确解答",
  "standard_answer": "供比对的最终答案，或 null",
  "topics": ["从词表里选的考点"],
  "error_causes": ["从词表里选的错因"],
  "new_tag_proposals": [],
  "confidence": {
    "bbox": 0.0, "problem_transcript": 0.0, "original_answer": 0.0,
    "original_solution": 0.0, "correct_solution": 0.0, "standard_answer": 0.0, "tags": 0.0
  },
  "notes": "任何让你为难、看不清或需要人确认的地方"
}"""

JUDGE_SYSTEM = """你在判断一次错题重做的结果。你会收到一张照片，上面是学生手写的作答。

只判**最终答案**，不判过程。输出一个 JSON：
{"verdict": "correct | wrong | unreadable", "confidence": 0.0, "reason": "一句话"}

铁律：
1. 数学上等价但写法不同，必须判 correct。例如 2/√3 与 2√3/3 等价；x=1/2 与 0.5 等价。
2. 看不清、被涂改盖住、或答案根本没写，判 unreadable——不要为了给出结论而猜。
   猜错的代价是把"其实还不会"的题标记成"已掌握"，这比判错方向更严重。
3. 只判这一道题；若照片里有多个作答，选与该题最匹配的一个，并在 reason 里说明。"""

EQUIV_GEN_SYSTEM = """你是一个数学等价性测试的出题人。给定一个标准答案，请生成它的测试用例。

输出一个 JSON：
{"equivalent": ["3 个与该答案数学等价、但写法不同的字符串"],
 "different": ["2 个与它接近但数学上不等价的字符串"]}

要求：这些字符串要像一个高中生真的会写在纸上的东西（含分数、根号、π、区间、集合等常见写法）。
不要给出任何提示或解释，只要字符串本身。"""

EQUIV_JUDGE_SYSTEM = """你要判断两个数学答案是否**等价**（数学上相同，写法可以不同）。

输出一个 JSON：{"equivalent": true, "confidence": 0.0, "reason": "一句话"}

只有数学上完全相同才算等价。举例：
- "2/√3" 与 "2√3/3" → 等价
- "x=2" 与 "x=±2" → 不等价
- "(1,2)" 与 "[1,2]" → 不等价
- "1/2" 与 "0.5" → 等价"""


# ---------------------------------------------------------------- 抽取

def validate_extraction(raw: dict, causes: list[str], topics: list[str]) -> dict:
    """把模型的自由发挥收回到受控词表与合法取值里，并把违规记录下来（这本身就是一项测量）。"""
    warn: list[str] = []

    bbox = raw.get("problem_bbox_norm")
    if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox)):
        warn.append(f"bbox 不可用（{bbox!r}）→ 退化为整页")
        bbox = [0.0, 0.0, 1.0, 1.0]
    else:
        x, y, w, h = [min(1.0, max(0.0, float(v))) for v in bbox]
        if w <= 0.02 or h <= 0.01:
            warn.append(f"bbox 太小（{bbox!r}）→ 退化为整页")
            x, y, w, h = 0.0, 0.0, 1.0, 1.0
        bbox = [x, y, w, h]

    ptype = raw.get("problem_type")
    if ptype not in ("choice", "fillin", "solution"):
        warn.append(f"题型非法（{ptype!r}）→ 记为 solution（保守：走人工确认）")
        ptype = "solution"

    picked_topics = [t for t in (raw.get("topics") or []) if t in topics]
    picked_causes = [c for c in (raw.get("error_causes") or []) if c in causes]
    proposals = list(raw.get("new_tag_proposals") or [])
    for t in (raw.get("topics") or []):
        if t not in topics:
            proposals.append(t)
            warn.append(f"考点越出词表：{t}")
    for c in (raw.get("error_causes") or []):
        if c not in causes:
            proposals.append(c)
            warn.append(f"错因越出词表：{c}")

    std = raw.get("standard_answer")
    if ptype in ("choice", "fillin") and not std:
        warn.append("可机判题型却没有标准答案 → 该题只能人工确认")
    if ptype == "solution" and std:
        warn.append("解答题却给了标准答案 → 已保留，但按设计仍走人工确认")

    conf = raw.get("confidence") or {}
    oa = raw.get("original_answer")
    corr = raw.get("correction_transcript")

    # 原答是这张卡上最危险的一个字段——它决定判对还是判错。所以不靠模型自觉，代码兜底三条。
    if oa and len(str(oa).strip()) == 1 and str(oa).strip().upper() in str(corr or "").upper():
        warn.append(f"原答 {oa!r} 也出现在订正转录里 → 很可能把订正误当成了原答，必须人工确认")
    if oa and std and str(oa).strip().upper() == str(std).strip().upper():
        warn.append(f"原答与标准答案相同（{oa!r}）→ 这是错题本，请确认这道题确实做错过")
    conf_oa = conf.get("original_answer")
    if oa and (not isinstance(conf_oa, (int, float)) or conf_oa < 0.6):
        warn.append(f"原答 {oa!r} 的置信度只有 {conf_oa}（< 0.6）→ 已置为 null 交人工确认："
                    f"低置信度下猜一个答案，比留空有害得多")
        oa = None
    if corr and not oa:
        warn.append("照片上有订正/批注，但没有可信的原答 → 原答留 null，需人工确认"
                    "（订正里的正答不等于学生答对了）")

    # 选项结构：重做纸要靠文字重排选择题，所以没结构化的选项就排不出来
    options = []
    for o in (raw.get("options") or []):
        if isinstance(o, dict) and o.get("label"):
            options.append({"label": str(o["label"]), "text": str(o.get("text") or "")})
    if ptype == "choice" and len(options) < 2:
        warn.append("选择题没给出可用的 options 结构 → 重做纸排不出选项，需人工补")

    # LaTeX 粗检：$ 数量为奇数说明多半没闭合，印出来会毁掉整张重做纸
    tr = raw.get("problem_transcript") or ""
    if tr.count("$") % 2:
        warn.append(f"题面转录里的 $ 有 {tr.count('$')} 个（奇数）→ LaTeX 可能没闭合")

    return {
        "bbox": bbox,
        "problem_type": ptype,
        "problem_transcript": tr or None,
        "options": options,
        "original_solution_present": bool(raw.get("original_solution_present")),
        "original_answer": oa,
        "original_solution_transcript": raw.get("original_solution_transcript"),
        "correction_transcript": corr,
        "correct_solution": raw.get("correct_solution"),
        "standard_answer": std,
        "topics": picked_topics,
        "error_causes": picked_causes,
        "new_tag_proposals": sorted(set(proposals)),
        "confidence": {k: conf.get(k) for k in
                       ("bbox", "problem_transcript", "original_answer", "original_solution",
                        "correct_solution", "standard_answer", "tags")},
        "notes": raw.get("notes"),
        "warnings": warn,
    }


def cmd_extract(args) -> int:
    photo = Path(args.photo).resolve()
    if not photo.is_file():
        die(f"找不到照片：{photo}")
    causes, topics = load_vocab()

    PAGES.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(photo.read_bytes()).hexdigest()[:12]
    page = PAGES / f"{digest}{photo.suffix.lower()}"
    if not page.exists():
        page.write_bytes(photo.read_bytes())

    user_text = (
        f"考点词表（只能从中选）：{json.dumps(topics, ensure_ascii=False)}\n"
        f"错因词表（只能从中选）：{json.dumps(causes, ensure_ascii=False)}\n"
    )
    if args.note:
        user_text += f"照片提供者的说明：{args.note}\n"
    user_text += f"\n输出必须是这个形状的 JSON：\n{EXTRACT_JSON_SHAPE}"

    cfg = role_config("extract")
    info(f"→ 抽取中（{cfg['provider']} / {cfg['model']}）…")
    text, usage = chat(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(photo)}},
                ],
            },
        ],
        cfg["role"],
        tag="extract",
    )
    try:
        raw = extract_json(text)
    except Exception as e:
        die(f"模型没给出可解析的 JSON：{e}\n\n原文：\n{text[:1200]}")

    got = validate_extraction(raw, causes, topics)

    PROBLEMS.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    pid = f"p-{datetime.now().strftime('%Y%m%d')}-{digest[:6]}"
    asset = ASSETS / f"{pid}-problem.png"
    box = crop_problem(page, got["bbox"], asset)

    record = {
        "id": pid,
        "created_at": now_iso(),
        "source": {"page_image": str(page.relative_to(ROOT)), "bbox_norm": got["bbox"],
                   "bbox_px": list(box), "original_file": photo.name},
        "problem": {"image": str(asset.relative_to(ROOT)), "type": got["problem_type"],
                    "transcript": got["problem_transcript"], "options": got["options"]},
        "original_solution": {"present": got["original_solution_present"],
                              "original_answer": got["original_answer"],
                              "transcript": got["original_solution_transcript"],
                              "correction_transcript": got["correction_transcript"]},
        "correct_solution": {"text": got["correct_solution"], "source": "ai"},
        "standard_answer": {"value": got["standard_answer"],
                            "confidence": got["confidence"]["standard_answer"]},
        "topics": got["topics"],
        "error_causes": got["error_causes"],
        "new_tag_proposals": got["new_tag_proposals"],
        "review": {"status": "unreviewed", "reviewed_at": None},
        "attempts": [],
        "mastery": {"state": "in_pool", "streak": 0, "last_attempt_at": None},
        "provenance": {"role": cfg["role"], "provider": cfg["provider"],
                       "model": cfg["model"], "extract_warnings": got["warnings"],
                       "model_notes": got["notes"], "confidence": got["confidence"],
                       "usage": usage},
    }
    card_path = PROBLEMS / f"{pid}.json"
    if card_path.exists():
        # 同一张照片＝同一道题，所以题卡按照片哈希命名、会被覆盖。
        # 验收对比两个模型时最容易在这里丢证据，所以覆盖要出声。
        try:
            old = json.loads(card_path.read_text(encoding="utf-8"))
            prev = old.get("provenance", {})
            print(f"⚠ 覆盖已有题卡 {card_path.name}：它上次是 "
                  f"{prev.get('provider')}/{prev.get('model')} 在 {old.get('created_at')} 抽的。\n"
                  f"  同一张照片只留一张题卡。要做模型对比，先把旧题卡另存。",
                  file=sys.stderr)
        except Exception:
            print(f"⚠ 覆盖已有题卡 {card_path.name}", file=sys.stderr)
    card_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n题卡：data/problems/{pid}.json")
    print(f"题面图：data/assets/{pid}-problem.png  （裁剪自 {page.name}，像素框 {box}）")
    print(f"\n题型：{got['problem_type']}")
    print(f"考点：{got['topics'] or '（空）'}   错因：{got['error_causes'] or '（空）'}")
    print(f"标准答案：{got['standard_answer']!r}")
    print(f"置信度：{json.dumps(got['confidence'], ensure_ascii=False)}")
    print(f"\n题面转录：{(got['problem_transcript'] or '')[:200]}")
    if got["options"]:
        print("选项：" + "   ".join(f"{o['label']}. {o['text']}" for o in got["options"]))
    print(f"原答（学生自己给的）：{got['original_answer']!r}")
    print(f"原解转录：{(got['original_solution_transcript'] or '（照片上没有）')[:200]}")
    if got["correction_transcript"]:
        print(f"订正/批注转录：{got['correction_transcript'][:200]}")
    print(f"正解（前 240 字）：{(got['correct_solution'] or '')[:240]}")
    if got["new_tag_proposals"]:
        print(f"\n新标签提案（待你审核）：{got['new_tag_proposals']}")
    for w in got["warnings"]:
        print(f"  ! {w}")
    if got["notes"]:
        print(f"\n模型自述的难处：{got['notes']}")
    print(f"\n本次用量：{usage}")
    return 0


def crop_problem(page: Path, bbox, out: Path, pad: float = 0.015) -> tuple[int, int, int, int]:
    img = Image.open(page).convert("RGB")
    W, H = img.size
    x, y, w, h = bbox
    x0 = max(0, round((x - pad) * W))
    y0 = max(0, round((y - pad) * H))
    x1 = min(W, round((x + w + pad) * W))
    y1 = min(H, round((y + h + pad) * H))
    if x1 - x0 < 24 or y1 - y0 < 24:
        x0, y0, x1, y1 = 0, 0, W, H
    img.crop((x0, y0, x1, y1)).save(out)
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------- 判定

def load_problem(path: Path) -> tuple[dict, Path]:
    if not path.is_file():
        die(f"找不到题卡：{path}")
    return json.loads(path.read_text(encoding="utf-8")), path


VERDICT_CN = {"correct": "对", "wrong": "错", "unreadable": "看不清"}
CHANNEL_CN = {"paper": "纸上重做", "screen": "屏幕重做"}


def _as_dt(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def cooldown_until(record: dict):
    """冷却到什么时候。基准是「上次重做」，**从未重做过的题以录入时间起算**。

    这条来自 CONTEXT「默认打印清单」，也是这套规则里最容易被漏掉的一处：
    新录入的题当天重做（比如勾了"显示冷却中的题"先试打）照常记录，但不推进掌握。
    """
    base = _as_dt((record.get("mastery") or {}).get("last_attempt_at")) or _as_dt(record.get("created_at"))
    return base + timedelta(days=COOLDOWN_DAYS) if base else None


def is_cooling(record: dict, at=None) -> bool:
    u = cooldown_until(record)
    return bool(u and (at or datetime.now(timezone.utc)) < u)


def cooldown_days_left(record: dict, at=None) -> int:
    u = cooldown_until(record)
    at = at or datetime.now(timezone.utc)
    if not u or at >= u:
        return 0
    secs = (u - at).total_seconds()
    return max(1, int(secs // 86400) + (1 if secs % 86400 else 0))


def apply_attempt(record: dict, verdict: str, confidence=None, source: str = "人工确认",
                  error_causes=None, evidence: str = None, at=None,
                  channel: str = "paper", note: str = None) -> dict:
    """把一次重做写进题卡，并按既定规则更新掌握与冷却。**这套规则只有这一份实现。**

      · 判错 → 无条件清零并立刻回池（毕业取消）；冷却只挡「计入正确」，不挡这一条。
      · 判对 → 只有距上次重做 ≥ 冷却期（7 天）才计入连续正确；冷却期内的重做只是热身。
      · 看不清 → 记下这次重做，但既不推进也不清零（低置信度一律落向这里，绝不落向对）。
      · 连续 2 次计入的正确 → 掌握 → 毕业（退出默认打印清单）。

    两条容易写错的地方：判断冷却必须发生在更新 `last_attempt_at` **之前**；
    以及 `at` 必须可以外部给——纸上重做是几天里做的，标记却可能晚几天才做。
    """
    m = record.setdefault("mastery", {"state": "in_pool", "streak": 0, "last_attempt_at": None})
    at = _as_dt(at) if at else datetime.now(timezone.utc).astimezone()
    prev_base = _as_dt(m.get("last_attempt_at")) or _as_dt(record.get("created_at"))
    cooling = bool(prev_base and at < prev_base + timedelta(days=COOLDOWN_DAYS))
    # 同一天里"录入在下午、重做标记在当天"会让差值为负——读数该是 0 天，不是 -1 天
    gap_days = max(0, int((at - prev_base).total_seconds() // 86400)) if prev_base else None

    attempt = {"at": at.isoformat(timespec="seconds"), "channel": channel,
               "verdict": verdict, "source": source, "confidence": confidence,
               "error_causes": ([error_causes] if isinstance(error_causes, str)
                                else list(error_causes or [])), "note": note}
    if evidence:
        attempt["evidence_image"] = evidence
    record.setdefault("attempts", []).append(attempt)
    m["last_attempt_at"] = attempt["at"]

    base = {"verdict": verdict, "credited": False, "streak": int(m.get("streak") or 0),
            "state": m.get("state") or "in_pool", "cooling": cooling, "gap_days": gap_days,
            "confidence": confidence, "source": source}

    if verdict == "wrong":
        m["streak"] = 0
        m["state"] = "in_pool"
        m.pop("mastered_at", None)
        base.update(streak=0, state="in_pool",
                    note="判错：清零回池（毕业若存在则取消）")
        return base

    if verdict == "correct":
        if cooling:
            base["note"] = (f"判对但仍在冷却期（距上次重做 {gap_days} 天）：只热身，不计入掌握")
            return base
        m["streak"] = int(m.get("streak") or 0) + 1
        base.update(credited=True, streak=m["streak"])
        if m["streak"] >= MASTERY_STREAK:
            m["state"] = "graduated"
            m["mastered_at"] = attempt["at"]
            base.update(state="graduated",
                        note="判对且脱离冷却：连续正确达标 → 掌握 → 毕业，退出默认打印清单")
            return base
        base["note"] = f"判对且脱离冷却：连续正确 {m['streak']}/{MASTERY_STREAK}"
        return base

    base["note"] = "看不清：已记录这次重做，但既不推进也不清零"
    return base


def cmd_judge(args) -> int:
    record, path = load_problem(Path(args.problem).resolve())
    std = (record.get("standard_answer") or {}).get("value")
    ptype = record["problem"]["type"]
    if ptype == "solution" or not std:
        die(
            f"这道题按设计不走自动判定（题型={ptype}，标准答案={std!r}）。\n"
            "  解答/证明题与未审核题都只能人工确认——这正是「避免静默污染状态」的那道闸门。"
        )
    photo = Path(args.photo).resolve()
    if not photo.is_file():
        die(f"找不到照片：{photo}")

    cfg = role_config("judge")
    info(f"→ 判定中（{cfg['provider']} / {cfg['model']}），标准答案：{std!r}")
    text, usage = chat(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": f"这道题的标准答案是：{std}\n下面这张照片是学生重做后的手写作答。请判断。"},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(photo)}},
                ],
            },
        ],
        cfg["role"],
        tag="judge",
    )
    try:
        out = extract_json(text)
    except Exception as e:
        die(f"模型没给出可解析的 JSON：{e}\n\n原文：\n{text[:1200]}")

    verdict = out.get("verdict")
    if verdict not in ("correct", "wrong", "unreadable"):
        die(f"判定结果非法：{verdict!r}（原文 {text[:300]}）")

    PAGES.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(photo.read_bytes()).hexdigest()[:12]
    evidence = PAGES / f"redo-{digest}{photo.suffix.lower()}"
    if not evidence.exists():
        evidence.write_bytes(photo.read_bytes())

    effect = apply_attempt(record, verdict, out.get("confidence"),
                           args.source, args.cause, str(evidence.relative_to(ROOT)))
    record["attempts"][-1]["model"] = f'{cfg["provider"]}/{cfg["model"]}'
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n判定：{verdict}   置信度：{out.get('confidence')}")
    print(f"理由：{out.get('reason')}")
    print(f"状态：{effect['note']}")
    print(f"掌握：{json.dumps(record['mastery'], ensure_ascii=False)}")
    print(f"本次用量：{usage}")
    if verdict == "correct" and record["mastery"]["state"] != "graduated":
        print("\n提示：一次正确不等于掌握。这就是纸上重做必须跨天重复的原因。")
    return 0


# ---------------------------------------------------------------- 等价性用例组

def cmd_equiv(args) -> int:
    cfg = role_config("judge")

    if args.cases:
        # 固定考卷：用例由人写，不走"模型自己出题自己考"
        blob = json.loads(Path(args.cases).read_text(encoding="utf-8"))
        std = blob.get("standard_answer")
        plan = [(c["answer"], bool(c["equivalent"])) for c in blob.get("cases", [])]
        pid = Path(args.cases).stem
        if not std or not plan:
            die(f"考卷格式不对（需要 standard_answer 与 cases）：{args.cases}")
        info(f"→ 固定考卷 {args.cases}，标准答案：{std!r}，用例 {len(plan)} 个")
    else:
        if not args.problem:
            die("要么给一个题卡（由模型出题），要么用 --cases 指定一份人手写的固定考卷。")
        record, _ = load_problem(Path(args.problem).resolve())
        std = (record.get("standard_answer") or {}).get("value")
        pid = record.get("id")
        if not std:
            die("这道题没有标准答案，等价性用例组无从构造。")

        info(f"→ 生成测试用例（{cfg['provider']} / {cfg['model']}），标准答案：{std!r}")
        text, _ = chat(
            [{"role": "system", "content": EQUIV_GEN_SYSTEM},
             {"role": "user", "content": f"标准答案：{std}"}],
            cfg["role"], tag="equiv-gen",
        )
        try:
            cases = extract_json(text)
        except Exception as e:
            die(f"用例生成失败：{e}\n\n原文：\n{text[:800]}")

        plan = [(s, True) for s in cases.get("equivalent", [])][:3] + \
               [(s, False) for s in cases.get("different", [])][:2]
        if len(plan) < 5:
            die(f"用例不足 5 个，模型只给了：{json.dumps(cases, ensure_ascii=False)}")

    rows, passed = [], 0
    for variant, expected in plan:
        # 每个用例单独一次请求：不让模型看到自己刚生成的用例，避免它顺着自己的话答
        info(f"  · 判定 {variant!r} …")
        out_text, _ = chat(
            [{"role": "system", "content": EQUIV_JUDGE_SYSTEM},
             {"role": "user", "content": f'标准答案："{std}"\n待判答案："{variant}"\n两者是否等价？'}],
            cfg["role"], tag="equiv-judge",
        )
        try:
            out = extract_json(out_text)
            got = bool(out.get("equivalent"))
            conf = out.get("confidence")
        except Exception:
            got, conf = None, None
        ok = got is expected
        passed += ok
        rows.append({"variant": variant, "expected_equivalent": expected,
                     "model_says": got, "confidence": conf, "pass": ok})

    print(f"\n等价性判定：{passed}/{len(plan)} 通过")
    for r in rows:
        mark = "✓" if r["pass"] else "✗"
        print(f"  {mark} {r['variant']!r:24} 期望等价={r['expected_equivalent']!s:5} "
              f"模型判={r['model_says']!s:5} 置信度={r['confidence']}")

    RUNS.mkdir(parents=True, exist_ok=True)
    report = RUNS / f"equiv-{pid}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    report.write_text(
        json.dumps({"problem": pid, "standard_answer": std, "rows": rows,
                    "passed": passed, "total": len(plan)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告：{report.relative_to(ROOT)}")
    print(f"通过标准是 {len(plan)}/{len(plan)} 全对；只要有一例把不等价判成等价，"
          f"你没学会的东西就可能被判成已掌握。")
    return 0 if passed == len(plan) else 1


# ---------------------------------------------------------------- models

def cmd_models(args) -> int:
    for role in ("extract", "judge"):
        cfg = role_config(role)
        print(f"[{role}] provider={cfg['provider']}  model={cfg['model']}")
        key = os.environ.get(cfg["key_env"], "").strip()
        if not key:
            print(f"    · 缺 {cfg['key_env']}，列不出这家 provider 的模型")
            continue
        r = requests.get(
            f'{cfg["base_url"]}/models',
            headers={"Authorization": f"Bearer {key}"},
            timeout=60,
        )
        if r.status_code >= 400:
            print(f"    ! HTTP {r.status_code}：{r.text[:200]}")
            continue
        ids = sorted(m.get("id", "") for m in r.json().get("data", []))
        data = r.json().get("data", [])
        if not data:
            print("    · 接口没返回任何模型")
            continue
        print(f"    · 接口返回 {len(data)} 个模型（能不能吃图看 input_modalities，不看名字）：")
        for m in data:
            mods = m.get("input_modalities") or []
            mark = "✓ 能吃图" if "image" in mods else "✗ 不能吃图"
            print(f"        {str(m.get('id')):<34} {mark}  模态={','.join(mods) or '?'}"
                  f"  上下文={m.get('context_window', '?')}")
        if cfg["model"] not in ids:
            print(f"    ! 当前角色配的 {cfg['model']!r} 不在这份列表里。"
                  f"实验模型不保证被列出，所以型号到底能不能用，只能靠 probe 说话。")
    print("\n提醒：换模型必须重跑验收（probe → 该角色的通过标准），不过考不许上岗。")
    return 0


# ---------------------------------------------------------------- 裁剪检查

def _ink_masks(path: Path):
    """拆成「深色墨迹」（印刷体候选，但黑笔手写也是深色）与「高饱和墨迹」（彩笔手写/订正）。"""
    im = Image.open(path).convert("RGB")
    r, g, b = im.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mn = ImageChops.darker(ImageChops.darker(r, g), b)
    sat = ImageChops.subtract(mx, mn)
    dark = ImageChops.multiply(mx.point(lambda v: 255 if v < 120 else 0),
                               sat.point(lambda v: 255 if v < 40 else 0))
    color = sat.point(lambda v: 255 if v >= 60 else 0)
    return im, dark, color


def _ink(mask, box) -> int:
    return mask.crop(box).histogram()[255]


def cmd_cropcheck(args) -> int:
    """裁剪框体检：不用眼睛也能查出「框切掉了内容」与「框里混进了手写订正」。

    已知弱点（必须记住）：黑笔手写与印刷体在灰度上同色，机器分不开。
    所以「解答题的框下方有一大片墨迹」是正常的——那正是该被排除的手写解答。
    这个检查能真正报警的只有两件事：框外的相邻条带里还有成片墨迹，以及框内出现了彩笔手写。
    """
    cards = [Path(p) for p in args.problems] or sorted(PROBLEMS.glob("*.json"))
    if not cards:
        die("没有题卡可查：先用 extract 抽一道，或直接给题卡路径。")
    flagged = 0
    for card_path in cards:
        rec = json.loads(card_path.read_text(encoding="utf-8"))
        page = ROOT / rec["source"]["page_image"]
        if not page.is_file():
            print(f"· {card_path.name}：找不到页面图 {page}，跳过")
            continue
        im, dark, color = _ink_masks(page)
        W, H = im.size
        x0, y0, x1, y1 = rec["source"]["bbox_px"]
        ptype = rec["problem"]["type"]
        area = max(1, (x1 - x0) * (y1 - y0))
        in_dark, in_color = _ink(dark, (x0, y0, x1, y1)), _ink(color, (x0, y0, x1, y1))
        right = _ink(dark, (x1, y0, W, y1))
        right_area = max(1, (W - x1) * (y1 - y0))
        below = _ink(dark, (x0, y1, x1, H))
        below_area = max(1, (x1 - x0) * (H - y1))

        flags = []
        if in_dark / area < 0.001:
            flags.append("框内几乎空白——框错了")
        if in_color > 20:
            flags.append(f"框内有彩笔手写 {in_color}px——重做纸会把它一起印出来"
                         f"（订正常常是红笔，正是最不该印上重做纸的东西）")
        if right / right_area > 0.005:
            flags.append(f"右边界外还有成片墨迹 {right}px（占该条带 {100*right/right_area:.1f}%）"
                         f"——可能切掉了题干或选项")
        if ptype != "solution" and below / below_area > 0.005:
            flags.append(f"下边界外有成片墨迹 {below}px（占该条带 {100*below/below_area:.1f}%）"
                         f"——{ptype} 题的正文可能被切掉了")

        print(f"· {card_path.name}  题型={ptype}  页面{W}x{H}  框=({x0},{y0},{x1},{y1})")
        print(f"    框内：深色墨迹 {in_dark}px（{100*in_dark/area:.1f}%）  彩笔 {in_color}px")
        print(f"    框右外同高条带 {right}px（{100*right/right_area:.1f}%）  "
              f"框下外 {below}px（{100*below/below_area:.1f}%）"
              + ("  ← 解答题：下方是手写解答，本就该排除" if ptype == "solution" and below else ""))
        if flags:
            flagged += 1
            for f in flags:
                print(f"    ⚠ {f}")
        else:
            print("    ✓ 没查出问题")
    print(f"\n共查 {len(cards)} 张，{flagged} 张需要看一眼。")
    print("提醒：它只能发现「框小了」和「框里混进彩笔」，不能确认框是否恰好等于题干。")
    return 0


# ---------------------------------------------------------------- 擦除手写

def _np_masks(img: Image.Image):
    """返回 (array, 彩笔掩膜, 深色掩膜)。彩笔＝高饱和；深色＝暗且低饱和（印刷体候选，黑笔也在内）。"""
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    mx, mn = a.max(axis=2), a.min(axis=2)
    sat = mx - mn
    return a, sat >= 60, (mx < 120) & (sat < 40)


def _local_max(a: np.ndarray, k: int) -> np.ndarray:
    """k×k 窗口的逐通道最大值。用移位取最大实现，避免引入 OpenCV/scipy。

    补零而不是补边缘：边缘处的像素可能已被置零（属于掩膜），补边缘会把 0 复制出去，
    让贴着图像边界的框在邻域里找不到纸色，从而填出黑边——这个 bug 真发生过。
    """
    r = k // 2
    h, w = a.shape[:2]
    p = np.pad(a, ((r, r), (r, r), (0, 0)), mode="constant")
    out = p[r:r + h, r:r + w].copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out = np.maximum(out, p[r + dy:r + dy + h, r + dx:r + dx + w])
    return out


HANDWRITING_SYSTEM = """你会收到一张题目的照片。请把所有**手写字迹**的位置标出来：学生的解答、演算、圈选、订正、批注，不论什么颜色都算。

输出一个 JSON：
{"handwriting": [{"bbox_norm": [x, y, w, h], "what": "一句话说明这里写的是什么"}]}

归一化坐标，原点在左上角，取值 0~1。铁律：

1. **只标手写，绝不包含印刷体**（题干、题号、选项、表格、印刷的图）。宁可漏标，也不要把印刷体框进去——
   擦掉印刷体就等于改变题目，比擦不干净更糟。
2. 框要贴着笔迹，不要框进大片空白。
3. 不确定的地方不要标。
4. 没有任何手写就回 {"handwriting": []}。"""


def erase_ink(img: Image.Image, boxes_px=None, add_px=None, drop_px=None,
              grow: int = 3, window: int = 31):
    """擦掉手写：彩笔靠颜色，黑笔靠模型框，人工可增可减。

    填补用「邻域里最亮的未掩膜像素」——纸是画面里最亮的东西，所以那就是纸色的估计。
    不需要修复模型，也不需要 OpenCV。
    返回 (擦除后的图, 掩膜可视化, 统计)。
    """
    a, colored0, dark = _np_masks(img)
    h, w = colored0.shape

    def paint(dst, rects, value):
        for (x0, y0, x1, y1) in _rects_px(rects):
            dst[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = value

    mask_arr = colored0.copy()
    paint(mask_arr, boxes_px, True)          # 模型标的框
    paint(mask_arr, add_px, True)            # 人工补的
    before_drop = int(mask_arr.sum())
    paint(mask_arr, drop_px, False)          # 人工去掉的
    colored = mask_arr
    colored_px, mask_px = int(colored0.sum()), int(mask_arr.sum())
    dropped_px = before_drop - mask_px
    erased_dark = int((dark & mask_arr).sum())
    total_dark = max(1, int(dark.sum()))
    mask = Image.fromarray((colored * 255).astype(np.uint8))
    grown = mask.filter(ImageFilter.MaxFilter(2 * grow + 1))     # 吃掉抗锯齿边缘
    alpha = np.asarray(grown.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32) / 255.0

    base = a.astype(np.int16).copy()
    base[alpha > 0] = 0                                           # 掩膜像素不参与背景估计
    bg = _local_max(base, window)
    # 整个窗口都被掩膜盖住时，局部最大值退化成 0；用全局纸色兜底，别填成黑的
    paper = int(np.percentile(a.max(axis=2), 90))
    hole = bg.max(axis=2) < paper * 0.75
    if hole.any():
        bg[hole] = paper
    out = (a * (1 - alpha[..., None]) + bg * alpha[..., None]).clip(0, 255).astype(np.uint8)

    # 两张可审的图：掩膜（红），以及彩笔压在印刷体上的位置（黄）——那些地方印刷体可能有缺口
    vis = np.asarray(img.convert("RGB")).copy()
    vis[colored] = [255, 0, 0]
    pressed = colored & np.asarray(
        Image.fromarray((dark * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
    ) > 0
    vis[pressed] = [255, 200, 0]

    out_img = Image.fromarray(out)
    _, residual, _ = _np_masks(out_img)
    stats = {
        "colored_px": colored_px,                    # 纯靠颜色认出的彩笔像素
        "mask_px": mask_px,                          # 实际被擦的区域（颜色 ∪ 模型框）
        "colored_ratio": round(mask_px / (h * w), 4),
        "boxes": len(boxes_px or []),
        "print_pressed_px": int(pressed.sum()),      # 彩笔压在印刷体上的规模＝可能有缺口的规模
        "erased_dark_px": erased_dark,               # 被擦掉的深色像素（印刷体或黑笔，机器分不开）
        "erased_dark_ratio": round(erased_dark / total_dark, 4),
        "dropped_px": dropped_px,                    # 人工从掩膜里去掉的面积
        "residual_px": int(residual.sum()),          # 擦完还剩多少彩笔像素，应接近 0
        "grow": grow, "window": window,
    }
    return out_img, Image.fromarray(vis), stats


def _rects_px(rects):
    return [tuple(int(round(v)) for v in r[:4]) for r in (rects or [])]


def norm_to_px(size, rects):
    """归一化小框 → 像素框。坐标一律归一化存盘，所以换分辨率也能重放。"""
    W, H = size
    out = []
    for b in (rects or []):
        if not (isinstance(b, (list, tuple)) and len(b) >= 4):
            continue
        x, y, ww, hh = [min(1.0, max(0.0, float(v))) for v in b[:4]]
        if ww > 0 and hh > 0:
            out.append((round(x * W), round(y * H), round((x + ww) * W), round((y + hh) * H)))
    return out


def apply_clean(img: Image.Image, auto_norm=None, manual=None, grow: int = 3):
    """按「自动框 ∪ 人工补 − 人工减」重算擦除。掩膜规格是数据的一部分，所以这一步可重放。"""
    manual = manual or {}
    return erase_ink(
        img,
        boxes_px=norm_to_px(img.size, auto_norm),
        add_px=norm_to_px(img.size, manual.get("add")),
        drop_px=norm_to_px(img.size, manual.get("drop")),
        grow=grow,
    )


def clean_health(orig: Image.Image, clean: Image.Image) -> dict:
    """确定性闸门（模型的判词只当线索，这一组当判据）。

    - 新出现的深色像素：擦除只该让像素变亮，凭空多出墨迹就是填补 bug（这个抓到过真 bug）。
    - 残留彩笔像素：应 0，否则没擦净。
    - 疑被咬掉一口的印刷体：被擦掉的墨迹若**左右两侧都还有墨迹**，它像是落在印刷笔画中间，
      那不是"擦掉了一片手写"，而是"在印刷体上啃了个洞"。单纯统计"墨迹减少的行"不行——
      擦掉一行手写本来就会让那一行墨迹减少，那是预期结果，不是损伤。
    """
    a0 = np.asarray(orig.convert("RGB"), dtype=np.int16)
    a1 = np.asarray(clean.convert("RGB"), dtype=np.int16)

    def dark(a):
        return (a.max(axis=2) < 120) & ((a.max(axis=2) - a.min(axis=2)) < 40)

    d0, d1 = dark(a0), dark(a1)
    lost = d0 & ~d1
    left = np.zeros_like(d0)
    right = np.zeros_like(d0)
    for k in range(2, 13):
        left |= np.roll(d1, k, axis=1)
        right |= np.roll(d1, -k, axis=1)
    holes = lost & left & right
    r0, r1 = d0.sum(axis=1), d1.sum(axis=1)
    _, residual, _ = _np_masks(clean)
    return {
        "dark_before": int(d0.sum()), "dark_after": int(d1.sum()),
        "new_dark_px": int((d1 & ~d0).sum()),        # 应 0
        "residual_color_px": int(residual.sum()),    # 应 0
        "print_holes_px": int(holes.sum()),          # 疑在印刷体上啃出的洞
        "rows_ink_reduced": int(((r0 >= 10) & (r1 < r0 * 0.7)).sum()),  # 仅供参考：擦手写也会减少
        "lum_before": round(float(a0.mean()), 1), "lum_after": round(float(a1.mean()), 1),
    }


def _clean_probes(cfg, clean_path: Path):
    """擦除是两侧都危险的：还剩手写＝泄露，擦过头＝改变题意。所以两侧都要探。"""
    def ask(q, tag):
        text, _ = chat(
            [{"role": "user", "content": [
                {"type": "text", "text": q},
                {"type": "image_url", "image_url": {"url": image_to_data_url(clean_path)}},
            ]}],
            cfg["role"], tag=tag,
        )
        return text.strip()

    leak = ask("这张图上有手写字迹吗？如果有，把手写内容逐字读出来；"
               "如果完全没有手写，只回两个字：没有。", "clean-verify-leak")
    dmg = ask("这张图上的印刷体文字有没有残缺、缺失或被涂抹掉的痕迹？"
              "如果印刷体完整，只回两个字：完整；如果有残缺，指出位置和内容。", "clean-verify-damage")
    return ("没有" in leak[:12]), leak, ("完整" in dmg[:12]), dmg


def cmd_clean(args) -> int:
    card_path = Path(args.problem).resolve()
    rec, _ = load_problem(card_path)
    src = ROOT / rec["problem"]["image"]
    if not src.is_file():
        die(f"找不到题面图：{src}")

    img = Image.open(src).convert("RGB")
    W, H = img.size
    cfg = role_config("extract")

    old_clean = rec["problem"].get("clean") or {}
    manual = old_clean.get("manual") or {}
    boxes_norm, boxes_px = [], []
    if args.boxes:
        info(f"→ 手写定位（{cfg['provider']} / {cfg['model']}）…")
        text, _ = chat(
            [{"role": "system", "content": HANDWRITING_SYSTEM},
             {"role": "user", "content": [
                 {"type": "text", "text": "标出这张图里所有手写字迹的位置。"},
                 {"type": "image_url", "image_url": {"url": image_to_data_url(src)}},
             ]}],
            cfg["role"], tag="locate-handwriting",
        )
        try:
            got = extract_json(text)
        except Exception as e:
            die(f"手写定位没给出可解析的 JSON：{e}\n\n原文：\n{text[:1200]}")
        for b in (got.get("handwriting") or []):
            bb = b.get("bbox_norm")
            if isinstance(bb, list) and len(bb) == 4 and all(isinstance(v, (int, float)) for v in bb):
                x, y, ww, hh = [min(1.0, max(0.0, float(v))) for v in bb]
                if ww > 0.005 and hh > 0.005:
                    boxes_norm.append([x, y, ww, hh])
                    box = (round(x * W), round(y * H), round((x + ww) * W), round((y + hh) * H))
                    boxes_px.append(box)
                    print(f"    · 框 {box}  {b.get('what', '')}")
        if not boxes_px:
            info("    （模型没标出任何手写）")
    else:
        boxes_norm = old_clean.get("boxes_norm") or []   # 没重新定位就沿用上次的，保证可重放

    out_img, vis, stats = erase_ink(
        img, boxes_px=norm_to_px(img.size, boxes_norm),
        add_px=norm_to_px(img.size, manual.get("add")),
        drop_px=norm_to_px(img.size, manual.get("drop")),
        grow=args.grow,
    )

    ASSETS.mkdir(parents=True, exist_ok=True)
    clean_path = ASSETS / f"{rec['id']}-clean.png"
    mask_path = ASSETS / f"{rec['id']}-cleanmask.png"
    out_img.save(clean_path)
    vis.save(mask_path)

    print(f"\n→ 擦除：{src.name}（{W}x{H}）")
    print(f"  彩笔像素 {stats['colored_px']}   模型框 {stats['boxes']} 个   "
          f"实擦区域 {stats['mask_px']}px（占 {stats['colored_ratio'] * 100:.1f}%）")
    print(f"  被擦掉的深色像素 {stats['erased_dark_px']}"
          f"（占全图深色墨迹的 {stats['erased_dark_ratio'] * 100:.1f}%）"
          f"——印刷体与黑笔手写机器分不开，这个数越大越要看图")
    health = clean_health(img, out_img)
    print(f"  客观闸门：新出现深色 {health['new_dark_px']}（应 0）"
          f" · 残留彩笔 {health['residual_color_px']}（应 0）"
          f" · 疑咬印刷体 {health['print_holes_px']}px（应 0）"
          f" · 墨迹减少的行 {health['rows_ink_reduced']}（擦手写也会减少，仅供参考）")
    if stats["mask_px"] == 0:
        print("  ⚠ 什么都没擦：这张图的框内没有彩笔，也没标出手写。"
              "若框内本就有黑笔手写，那是定位这一步漏了。")
    print(f"\n  擦除后：{clean_path.relative_to(ROOT)}")
    print(f"  掩膜图（红＝判定为手写，黄＝手写压在印刷体上的位置）：{mask_path.relative_to(ROOT)}")

    verify = {}
    if args.verify:
        info(f"\n→ 擦除验收（{cfg['provider']} / {cfg['model']}）两侧都探")
        leak_ok, leak_txt, dmg_ok, dmg_txt = _clean_probes(cfg, clean_path)
        verify = {"leak_ok": leak_ok, "damage_ok": dmg_ok,
                  "leak_says": leak_txt[:300], "damage_says": dmg_txt[:300]}
        print(f"    {'✓ 通过' if leak_ok else '✗ 没通过'} 还剩手写吗：{leak_txt[:200]!r}")
        print(f"    {'✓ 通过' if dmg_ok else '✗ 没通过'} 印刷体完整吗：{dmg_txt[:200]!r}")
        if not leak_ok:
            print("    还剩手写＝重做纸仍会泄露内容（危险方向之一）。")
        if not dmg_ok:
            print("    印刷体被擦坏＝题意变了（危险方向之二）。")

    if args.save:
        rec["problem"]["clean_image"] = str(clean_path.relative_to(ROOT))
        rec["problem"]["clean"] = {
            "method": "erase_ink", "boxes_norm": boxes_norm, "manual": manual,
            "health": health, **stats, "verify": verify,
        }
        card_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写回题卡：{card_path.name}（problem.clean_image / problem.clean）")
    return 0


# ---------------------------------------------------------------- 验收

def cmd_probe(args) -> int:
    """视觉探针：验收第一关。

    防的是最阴的一种失败：配置指到纯文本模型时它不会报错，只会忽略图片、
    转而照着提示词把题卡编出来——你会得到一张格式正确、内容全是幻觉的题卡。
    """
    roles = ["extract", "judge"] if args.role == "both" else [args.role]
    all_ok = True
    for role in roles:
        cfg = role_config(role)
        secret = "".join(secrets.choice("0123456789") for _ in range(6))
        img = Image.new("RGB", (680, 260), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=104)
        except TypeError:  # 老版本 Pillow 没有 size 参数
            font = ImageFont.load_default()
        draw.text((70, 70), secret, fill="black", font=font)
        img = img.rotate(3, expand=True, fillcolor="white")  # 稍许倾斜，别让探针太好读
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"

        info(f"→ 探针 [{role}] {cfg['provider']} / {cfg['model']}：图里写着六位数 {secret}")
        text, _ = chat(
            [{"role": "user", "content": [
                {"type": "text", "text": "这张图里写着一个六位数。只回那个数字，不要别的字。"},
                {"type": "image_url", "image_url": {"url": url}},
            ]}],
            role, tag="probe",
        )
        ok = secret in re.sub(r"\D", "", text)
        all_ok &= ok
        print(f"    {'✓ 通过' if ok else '✗ 不通过'}：模型回的是 {text.strip()[:60]!r}")
    if not all_ok:
        print("\n探针不通过 = 这个模型不能上岗。别继续往下跑用例，先换模型。")
    return 0 if all_ok else 1


def cmd_accept(args) -> int:
    """验收 = 视觉探针 + 该角色的通过标准。"""
    rc = cmd_probe(argparse.Namespace(role=args.role))
    if rc != 0:
        return rc
    if args.problem:
        print("\n判定角色的通过标准：模型自出题与人写固定考卷都要全对")
        return cmd_equiv(argparse.Namespace(problem=args.problem, cases=None))
    print("\n抽取角色的通过标准要人判：跑 extract，数一数有几个字段你不用改（> 50% 才算过）。")
    return 0


# ---------------------------------------------------------------- 入口

def main() -> int:
    load_env()
    p = argparse.ArgumentParser(description="错题本纵向切片原型")
    p.add_argument("--extract-model", help="覆盖抽取角色的模型（等价于 EXTRACT_MODEL）")
    p.add_argument("--judge-model", help="覆盖判定角色的模型（等价于 JUDGE_MODEL）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="按角色列出 provider 与可用模型").set_defaults(
        func=cmd_models)

    pr = sub.add_parser("probe", help="视觉探针：验收第一关，防静默瞎编")
    pr.add_argument("--role", default="both", choices=["extract", "judge", "both"])
    pr.set_defaults(func=cmd_probe)

    ac = sub.add_parser("accept", help="验收：探针 + 该角色的通过标准")
    ac.add_argument("--role", default="both", choices=["extract", "judge", "both"])
    ac.add_argument("problem", nargs="?", help="判定角色需要一道有标准答案的题卡")
    ac.set_defaults(func=cmd_accept)

    e = sub.add_parser("extract", help="抽取一张照片里的一道题")
    e.add_argument("photo")
    e.add_argument("--note", help="这张照片的补充说明，例如“照片里是第 12 题”")
    e.set_defaults(func=cmd_extract)

    j = sub.add_parser("judge", help="判定一次重做（已停用：D2 翻案后纸上走手动标记）")
    j.add_argument("problem")
    j.add_argument("photo")
    j.add_argument("--source", default="auto", choices=["auto", "human"],
                   help="这条判定是自动判定还是人工确认")
    j.add_argument("--cause", help="若判错，记下的错因（取自受控词表）")
    j.set_defaults(func=cmd_judge)

    q = sub.add_parser("equiv", help="量一量等价性判定的可靠性（判定角色的通过标准）")
    q.add_argument("problem", nargs="?", help="题卡；不给则必须用 --cases")
    q.add_argument("--cases", help="人手写的固定考卷（含 standard_answer 与 cases）")
    q.set_defaults(func=cmd_equiv)

    cc = sub.add_parser("cropcheck", help="裁剪框体检：查框切掉内容、或框里混进手写订正")
    cc.add_argument("problems", nargs="*", help="题卡路径；不给就查 data/problems 下全部")
    cc.set_defaults(func=cmd_cropcheck)

    cl = sub.add_parser("clean", help="擦除手写：彩笔靠颜色，黑笔靠模型给的框")
    cl.add_argument("problem")
    cl.add_argument("--boxes", action="store_true", help="先让模型标出手写位置，再擦那些区域")
    cl.add_argument("--grow", type=int, default=3, help="掩膜外扩像素数，吃掉抗锯齿边缘")
    cl.add_argument("--verify", action="store_true", help="两侧探针：还剩手写吗、印刷体被擦坏了吗")
    cl.add_argument("--save", action="store_true", help="把 clean_image 与统计写回题卡")
    cl.set_defaults(func=cmd_clean)

    args = p.parse_args()
    if args.extract_model:
        os.environ["EXTRACT_MODEL"] = args.extract_model
    if args.judge_model:
        os.environ["JUDGE_MODEL"] = args.judge_model
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
