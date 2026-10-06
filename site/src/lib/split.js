/**
 * 切分修正界面的**纯逻辑**（工单 #14、spec #2「手动修正最小集合」）。
 *
 * 这一层只做三件事，全部可以在没有浏览器、没有服务的情况下断言：
 *
 *   1. **重切三态的显示**（验收 1）：`classify_resegment` 给的
 *      `matches[].state`（`kept`／`new`）与 `summary.replaced` 翻成中文原话。
 *      **界面必须显示三态**，不能只说「重切完成」——否则「已入库的块被替换了」
 *      这件事在界面上看不见，而那正是这条验收要防的。
 *   2. **两套坐标的换算**（D5 的坑）：把**块框画在整页照片上**与把**掩膜框画在
 *      题面裁剪图上**用的是两套基准。混用不会报错、只会**静默错位**，所以这里的
 *      换算与 `server/coords.py` 一一对应，并指名道姓地说清哪一个函数画哪一张图。
 *   3. **修正请求的形状**：七条动作拼成 `PATCH /api/page/<id>` 的 `edits`。
 *      界面**只提交「人做了什么」**（边界、块的 id、题号、题型、收不收），
 *      绝不提交判定字段——取舍规则只有服务那一处（#12）。
 *
 * **界面不碰照片处理**（spec #2：模型调用与图像统计都在服务内部）。这里一个像素
 * 都不读，只算坐标。
 *
 * 一条纪律（#8 的不变量同源）：**读不出来的框给 `null`，绝不回退成零框**
 * ——`(0,0,0,0)` 会静默画在左上角，而「不知道」与「在角上」是两件事。
 */

/** 重切三态的中文原话。`state` 的取值来自 `server/segmentation.py: classify_resegment`。 */
export const RESEGMENT_STATES = Object.freeze({
  kept: {
    label: '保留',
    detail: '位置重合，沿用原来的题卡（id 不变）',
    tone: 'kept',
  },
  new: {
    label: '新增',
    detail: '这一块是新切出来的，还没有题卡；入库那一刻才分配 id',
    tone: 'new',
  },
  replaced: {
    label: '替换',
    detail: '同一个位置换了一张卡——服务不会自动产生这个状态，看到它要当异常查',
    tone: 'replaced',
  },
});

/** 修正动作的中文原话。取值与 `server/page_edit.py: EDITABLE_ACTIONS` 一一对应。 */
export const EDIT_ACTIONS = Object.freeze({
  move: '拖边界',
  merge: '合并两块',
  split: '拆分一块',
  drop: '整块丢弃',
  keep: '收进库',
  type: '改题型',
  question_no: '改题号',
});

/** 题型枚举（契约 §3）。`null` = 还没定，与「解答题」不是一回事。 */
export const PROBLEM_TYPES = Object.freeze({
  choice: '选择题',
  fillin: '填空题',
  solution: '解答题',
});

// ---------------------------------------------------------------- 坐标换算

/**
 * 一个读得出来的 xywh 框（`[x, y, w, h]`，`w`/`h` > 0）→ 四个数；否则 `null`。
 *
 * 与 `server/pages.py: usable_box` 是**同一条判据**：形状不对或没有面积都算读不出来。
 * 给 `null` 而不是零框，理由见文件头。
 */
export function readBox(box) {
  if (!Array.isArray(box) || box.length !== 4) return null;
  const values = box.map(Number);
  if (values.some((v) => !Number.isFinite(v))) return null;
  const [x, y, w, h] = values;
  if (w <= 0 || h <= 0) return null;
  return [x, y, w, h];
}

/**
 * **块框画在整页照片上**：整页归一化 xywh → 显示尺寸下的像素 xywh。
 *
 * 块本来就是整页坐标（页文件的存储基准，D5），所以这里**只是缩放**，
 * 不做任何跨基准换算。`width`／`height` 是照片在界面上**显示出来的**尺寸。
 */
export function blockBoxToDisplay(box, width, height) {
  const read = readBox(box);
  if (read === null) return null;
  const w = Number(width);
  const h = Number(height);
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
  const [x, y, bw, bh] = read;
  return [x * w, y * h, bw * w, bh * h];
}

/**
 * **掩膜框画在题面裁剪图上**：裁剪图归一化 xywh → 显示尺寸下的像素 xywh。
 *
 * 掩膜（`clean.boxes_norm`／`manual`）本来就是**相对题面裁剪图**归一化的
 * （契约 §5），所以画在裁剪图上同样只是缩放。
 *
 * ⚠ **不要拿这个函数去画整页照片**。要「把掩膜画到整页上」，用
 * `maskBoxOntoPage`——两者差一个块的平移与缩放，混用会静默错位。
 */
export function maskBoxToCropDisplay(box, width, height) {
  return blockBoxToDisplay(box, width, height);
}

/**
 * **掩膜框画在整页照片上**：裁剪图归一化 xywh → 整页归一化 xywh。
 *
 * 先按块的尺寸缩放，再平移到块的原点。这是 `server/coords.py: crop_box_to_page`
 * 的界面侧对应物——**审核页要让人看见「这点红笔在整页的哪儿」时走这一条**。
 *
 * `clamp`（默认 true）把越出块边界的掩膜裁到块内（手工掩膜是人在裁剪图上拖的，
 * 拖出界是常事，画到页上会盖住邻居）。裁没了的框给 `null`。
 */
export function maskBoxOntoPage(box, blockBox, {clamp = true} = {}) {
  const read = readBox(box);
  const outer = readBox(blockBox);
  if (read === null || outer === null) return null;
  const [px, py, pw, ph] = outer;
  const [x, y, w, h] = read;
  let [nx, ny, nw, nh] = [px + x * pw, py + y * ph, w * pw, h * ph];
  if (clamp) {
    const x0 = Math.max(nx, px);
    const y0 = Math.max(ny, py);
    const x1 = Math.min(nx + nw, px + pw);
    const y1 = Math.min(ny + nh, py + ph);
    if (x1 <= x0 || y1 <= y0) return null;
    [nx, ny, nw, nh] = [x0, y0, x1 - x0, y1 - y0];
  }
  return [nx, ny, nw, nh];
}

/** 一串掩膜框 → 整页坐标（与输入同序；画不出来的那一项是 `null`，位置对得上）。 */
export function maskBoxesOntoPage(boxes, blockBox, options = {}) {
  return (boxes || []).map((box) => maskBoxOntoPage(box, blockBox, options));
}

/** 整页归一化 xywh → 整页像素 xywh（按**照片实际**尺寸；界面缩放的原料）。 */
export function pageBoxToPixels(box, width, height) {
  return blockBoxToDisplay(box, width, height);
}

/**
 * 两个整页归一化框的并集（合并两块时要显示的那个框）。全读不出来 → `null`。
 *
 * 与 `server/page_edit.py: union_box` 同一份算法：拖动合并是**服务**做的事
 * （界面只发请求），这里算并集是为了**在提交之前就把结果画出来给人看**。
 */
export function unionBox(boxes) {
  const good = (boxes || []).map(readBox).filter((box) => box !== null);
  if (good.length === 0) return null;
  const x0 = Math.min(...good.map((b) => b[0]));
  const y0 = Math.min(...good.map((b) => b[1]));
  const x1 = Math.max(...good.map((b) => b[0] + b[2]));
  const y1 = Math.max(...good.map((b) => b[1] + b[3]));
  return [x0, y0, x1 - x0, y1 - y0];
}

// ---------------------------------------------------------------- 重切三态的显示

/**
 * 重切报告 → 逐块的三态显示（**验收 1** 的界面落点）。
 *
 * 每一条给出：`state`（`kept`／`new`／`replaced`）、中文 `label`、`detail`、
 * `block_id`、`card_id`、以及「这块对应的卡人动过没有」。
 *
 * **`replaced` 只在服务真的报了它时才显示**：`classify_resegment` 的
 * `summary.replaced` 恒为 0（结构性的事实，有测试钉住），所以这里**不伪造**这个状态，
 * 但保留它的措辞——将来若真有哪条路径产生了它，界面要能显示并当异常看。
 */
export function resegmentRows(report) {
  const matches = (report && report.matches) || [];
  const blocks = (report && report.blocks) || [];
  const cardsById = new Map();
  for (const block of blocks) {
    if (block && block.card_id) cardsById.set(block.id, block.card_id);
  }
  return matches.map((match, index) => {
    const state = RESEGMENT_STATES[match.state] ? match.state : 'new';
    const known = RESEGMENT_STATES[state];
    return {
      index,
      block_id: match.block_id,
      state,
      label: known.label,
      detail: known.detail,
      tone: known.tone,
      card_id: cardsById.get(match.block_id) ?? null,
      matched_from: match.matched_from ?? null,
      iou: match.iou ?? null,
      contain: match.contain ?? null,
    };
  });
}

/**
 * 重切报告 → 一句给人看的总结 + 三档计数。
 *
 * **三档计数一个都不许省**（哪怕某一档是 0）：只显示「重切完成」正是这条验收要防的
 * ——「已入库的块被替换了」在那种界面上看不见。
 */
export function resegmentSummary(report) {
  const summary = (report && report.summary) || {};
  const counts = {
    kept: summary.kept || 0,
    new: summary.new || 0,
    replaced: summary.replaced || 0,
    removed: summary.removed || 0,
  };
  const parts = [`保留 ${counts.kept}`, `新增 ${counts.new}`, `替换 ${counts.replaced}`];
  if (counts.removed) parts.push(`消失 ${counts.removed}`);
  return {
    counts,
    line: parts.join(' · '),
    // 「要不要人看一眼」由**服务**判（`classify_resegment` 的 `needs_human`），
    // 界面不自己判一遍（#12 那条纪律的同一份口径）。
    needs_human: Boolean(summary.needs_human),
    human_work_checked: Boolean(summary.human_work_checked),
    ran: report ? report.ran !== false : false,
    segmentation_unavailable: Boolean(report) && report.segmentation === 'unavailable',
    wrote_cards: Boolean(report && report.wrote_cards),
    wrote_page: Boolean(report && report.wrote_page),
  };
}

/**
 * 重切报告里与卡片有关的警告（人动过的卡、消失的块带着卡）。
 *
 * 界面**照服务给的原话显示**，不自己拼一句（契约 §6.1 第 5 条的口径：
 * M/N 那类句子由服务给，界面重复拼一遍会出重复行——实测踩过）。
 */
export function resegmentCardWarnings(report) {
  const warnings = (report && report.warnings) || [];
  return warnings
    .filter((w) => w && (w.code === 'resegment_card_human_work'
      || w.code === 'block_removed_with_card'))
    .map((w) => ({code: w.code, id: w.id ?? null, level: w.level || 'warning',
      message: w.message}));
}

// ---------------------------------------------------------------- 修正请求的形状

/**
 * 一条修正 → `PATCH /api/page/<id>` 的 `edits` 里的一项。
 *
 * 界面**只提交「人做了什么」**：块的 id、新的边界、题号、题型、收不收。
 * 判定字段（`verdict`／`source`／`confidence`／`rule`）一个都不许由客户端给——
 * 取舍规则只有服务那一处（`server/intake.py`，#12）。
 *
 * 形状不对就**抛**（不是发一个服务一定拒绝的请求）：界面自己先挡住明显的手误，
 * 理由与 `server/page_edit.py` 的判据一致（题号必须是正整数等）。
 */
export function editPayload(action, fields = {}) {
  if (!EDIT_ACTIONS[action]) {
    throw new Error(`不认识的修正动作：${action}（可取值：${Object.keys(EDIT_ACTIONS).join('／')}）`);
  }
  switch (action) {
    case 'move': {
      const box = readBox(fields.bbox_norm);
      if (box === null) throw new Error('拖边界要给一个读得出来的 bbox_norm（xywh，宽高为正）');
      return {action, block_id: fields.block_id, bbox_norm: box};
    }
    case 'merge': {
      const ids = fields.block_ids || [];
      if (ids.length < 2) throw new Error('合并至少要两块');
      return {action, block_ids: [...ids]};
    }
    case 'split': {
      const boxes = (fields.boxes || []).map(readBox);
      if (boxes.length < 2 || boxes.some((b) => b === null)) {
        throw new Error('拆分要给至少两个读得出来的框');
      }
      const payload = {action, block_id: fields.block_id, boxes};
      // 题号**不给就是不猜**：服务会报 `page_split_question_no_unset`，界面把那一句显示出来。
      if (fields.question_numbers) payload.question_numbers = [...fields.question_numbers];
      return payload;
    }
    case 'drop':
    case 'keep':
      return {action, block_id: fields.block_id};
    case 'type':
      if (fields.problem_type !== null && !PROBLEM_TYPES[fields.problem_type]) {
        throw new Error(`题型 ${fields.problem_type} 不在枚举里（${Object.keys(PROBLEM_TYPES).join('／')}）`);
      }
      return {action, block_id: fields.block_id, problem_type: fields.problem_type ?? null};
    case 'question_no':
      if (fields.question_no !== null && !Number.isInteger(fields.question_no)) {
        throw new Error('题号要么是一个正整数、要么留空（不许猜一个）');
      }
      return {action, block_id: fields.block_id, question_no: fields.question_no ?? null};
    default:
      throw new Error(`修正动作 ${action} 还没有形状`);
  }
}

/** 一串修正 → `PATCH /api/page/<id>` 的 body。`dryRun` 预演（服务一个字节都不写）。 */
export function editsRequest(edits, {dryRun = false} = {}) {
  return {dry_run: Boolean(dryRun), edits};
}

/**
 * 一次修正的结果 → 给界面看的读数（**不许静默**：做了什么、没做什么都要看得见）。
 *
 * `changed=false`（幂等重复提交、或点了不在页上的块）时**明确说出来**，
 * 而不是显示一个像成功的样子——那正是这个项目反复被咬的那类失败。
 */
export function editOutcome(report) {
  const edits = (report && report.edits) || [];
  const warnings = (report && report.warnings) || [];
  const changedRows = edits.filter((e) => e.changed);
  const unchangedRows = edits.filter((e) => !e.changed);
  return {
    changed: Boolean(report && report.changed),
    applied: changedRows.length,
    noop: unchangedRows.length,
    // 每条动作一条原话（照服务给的中文显示，不重拼）
    rows: edits.map((edit, index) => ({
      index,
      action: edit.action,
      label: EDIT_ACTIONS[edit.action] || String(edit.action),
      changed: Boolean(edit.changed),
      blocks_changed: edit.blocks_changed || [],
      blocks_removed: edit.blocks_removed || [],
      notes: warnings
        .filter((w) => w && (edit.blocks_changed || []).includes(w.id))
        .map((w) => w.message),
    })),
    // 服务说「要人看一眼」的那些警告（原话带出来）
    warnings: warnings.map((w) => ({
      code: w.code, level: w.level || 'warning', id: w.id ?? null, message: w.message,
    })),
    wrote_cards: Boolean(report && report.wrote_cards),
    wrote_page: Boolean(report && report.wrote_page),
    dry_run: Boolean(report && report.dry_run),
  };
}

// ---------------------------------------------------------------- 页的视图

/**
 * 一块 → 界面上要画的东西（**两套坐标各画各的**）。
 *
 * 返回：
 * - `page_box`：块框在**整页照片**上的归一化 xywh（块本来就是整页坐标，原样给）；
 * - `masks_on_page`：掩膜框投到**整页照片**上的归一化 xywh（要**换算**——这是
 *   「这点红笔在整页的哪儿」）；
 * - `masks_in_crop`：掩膜框在**题面裁剪图**上的归一化 xywh（原样给——它本来就是
 *   裁剪图坐标）；
 * - `card_id`、`keep`、`question_no`、`problem_type` 等读数。
 *
 * **`keep` 是三态**：`true` 收 / `false` 不收 / `null` 待定。`null` 不许被显示成
 * 「不收」——那是静默丢题（`server/intake.py` 的同一条纪律）。
 */
export function blockView(block, {maskBoxes = []} = {}) {
  const pageBox = readBox(block && block.bbox_norm);
  const cropBoxes = (maskBoxes || []).map(readBox);
  return {
    id: block && block.id,
    card_id: (block && block.card_id) ?? null,
    page_box: pageBox,
    bbox_px: (block && block.bbox_px) ?? null,
    question_no: (block && block.question_no) ?? null,
    problem_type: (block && block.problem_type) ?? null,
    problem_type_cn: PROBLEM_TYPES[block && block.problem_type] || null,
    keep: block ? (block.keep ?? null) : null,
    keep_cn: keepLabel(block ? block.keep : null),
    decision: (block && block.decision) || null,
    ink: (block && block.ink) || null,
    masks_in_crop: cropBoxes,
    masks_on_page: pageBox === null ? [] : cropBoxes.map((b) => maskBoxOntoPage(b, pageBox)),
  };
}

/** 去留的三态原话。`null` 是**待定**，不是「不收」。 */
export function keepLabel(keep) {
  if (keep === true) return '收';
  if (keep === false) return '不收';
  return '待定';
}

/**
 * 一页 → 界面的读数（块列表 + 「没入库的题」的枚举）。
 *
 * 「没入库的题」**必须列出来**（#12 验收 1）：`keep !== true` 的每一块给 id、
 * 规则码与人话理由。「为什么这块没入库」在界面上要一眼看得到。
 */
export function pageView(page) {
  const blocks = (page && page.blocks) || [];
  const rows = blocks.filter((b) => b && typeof b === 'object').map((b) => blockView(b));
  const notKept = rows.filter((row) => row.keep !== true);
  return {
    page_id: (page && page.id) ?? null,
    image: (page && page.image) ?? null,
    origin: (page && page.origin) || null,
    blocks: rows,
    counts: {
      blocks: rows.length,
      kept: rows.filter((r) => r.keep === true).length,
      dropped: rows.filter((r) => r.keep === false).length,
      pending: rows.filter((r) => r.keep === null).length,
    },
    not_kept: notKept.map((row) => ({
      block_id: row.id,
      keep: row.keep,
      keep_cn: row.keep_cn,
      rule: (row.decision && row.decision.rule) || null,
      reason: (row.decision && row.decision.reason) || null,
    })),
  };
}

/**
 * 页文件里的块 → 「能不能自动判定」这一问**不在这里判**。
 *
 * 唯一实现在 `server/autojudge.py`（spec #2 的跨单元表：界面不许再写一遍）。
 * 这个函数存在只是为了把那条纪律写进代码里：它把服务给的读数原样带出来。
 */
export function autoJudgeReadout(problem) {
  const auto = (problem && problem.auto_judge) || null;
  if (!auto) return null;
  return {
    eligible: Boolean(auto.eligible),
    reason: auto.reason ?? null,
    reason_text: auto.reason_text ?? null,
  };
}
