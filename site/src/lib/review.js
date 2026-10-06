/**
 * 审核页的纯逻辑（一行 React 都没有；组件那一半在 `components/ReviewQueue.js`）。
 *
 * 三条形状上的事，都在这里定死：
 *
 * 1. **待审清单不新开端点**：`/api/index` 的逐卡警告里已经有 `problem_transcript_missing`
 *    ——按它筛就是"还没转录的卡"（契约 §10.8 第 7 条）。
 * 2. **草稿 → 表单 → 请求体**，中间那一步最容易写错：服务端把 `""` 解释成**显式清空**，
 *    所以**没动过、且草稿里也没有的空字段，一个字都不许发**。发了就等于清空人家的内容。
 * 3. **按时刻排序**，不按字符串：`created_at` 带偏移，字符串比会把时区差当成先后。
 */

const TEXT_FIELDS = ['transcript'];
const LIST_FIELDS = ['topics', 'error_causes'];

/** `created_at` → 可比时刻；读不出来的排最后（它们本来就进不了时间线）。 */
export function momentOf(createdAt) {
  const at = Date.parse(createdAt || '');
  return Number.isFinite(at) ? at : -Infinity;
}

/**
 * 外壳的索引状态 → 还没转录的卡（由新到老）。
 *
 * ⚠ 形状按**外壳真实给的那个**：`{phase, envelope, data, error}`——警告在
 * **`envelope.warnings`** 里，不在 `index.warnings`。我一开始按后者写，
 * 纯逻辑测试用的是我自己假设的夹具、于是全绿，**真浏览器里却是空清单**。
 * 所以这里只认一种形状（多认一种就是允许两种形状漂移）。
 */
export function pendingFromIndex(index) {
  const cards = new Map(((index && index.data && index.data.problems) || [])
    .map((card) => [card.id, card]));
  const ids = [];
  for (const warning of (index && index.envelope && index.envelope.warnings) || []) {
    if (warning.code === 'problem_transcript_missing' && warning.id && !ids.includes(warning.id)) {
      ids.push(warning.id);
    }
  }
  return ids
    .map((id) => ({id, ...(cards.get(id) || {})}))
    .sort((left, right) => momentOf(right.created_at) - momentOf(left.created_at));
}

/** 转录读数 → 表单状态。`touched` 决定"这一次要不要把它发回去"。 */
export function formFromReadout(readout) {
  const result = (readout && readout.result) || {};
  const form = {
    transcript: typeof result.transcript === 'string' ? result.transcript : '',
    topics: Array.isArray(result.topics) ? [...result.topics] : [],
    error_causes: Array.isArray(result.error_causes) ? [...result.error_causes] : [],
    standard_answer: result.standard_answer && result.standard_answer.value
      ? {value: result.standard_answer.value,
         confidence: result.standard_answer.confidence ?? null}
      : null,
    touched: {},
  };
  for (const field of [...TEXT_FIELDS, ...LIST_FIELDS, 'standard_answer']) {
    form.touched[field] = false;
  }
  return form;
}

/** 草稿里**非空**的字段（没人动过时，这些是"人点了确认"的东西）。 */
function draftNonEmpty(form) {
  const present = {};
  if (form.transcript.trim()) present.transcript = true;
  for (const field of LIST_FIELDS) {
    if (form[field].length > 0) present[field] = true;
  }
  if (form.standard_answer && form.standard_answer.value) present.standard_answer = true;
  return present;
}

/**
 * 表单 → `PUT /api/problem/<id>/transcript` 的请求体。
 *
 * **只发"人动过的"与"草稿里非空的"**：
 *
 * - 没动过、草稿里也没有的空字段 → **一个都不发**。服务端把 `""` 当成**显式清空**，
 *   发了就等于把内容清掉——这是这条路上最容易写错、后果又最静默的一处。
 * - 人动过的（哪怕动成了空）→ 发出去，包括 `""`：那是他明确说"这里没有"。
 */
export function buildTranscriptPayload(form, {markReviewed = false} = {}) {
  const present = draftNonEmpty(form);
  const payload = {};

  for (const field of TEXT_FIELDS) {
    if (form.touched[field] || present[field]) payload[field] = form[field];
  }
  for (const field of LIST_FIELDS) {
    if (form.touched[field] || present[field]) payload[field] = form[field];
  }
  if (form.touched.standard_answer || present.standard_answer) {
    payload.standard_answer = form.standard_answer;   // 对象或 null（null ＝ 清）
  }
  if (markReviewed) payload.mark_reviewed = true;
  return payload;
}

/** 有没有值得提交的东西（没有就让"保存"禁用，而不是让人按了再收到一句"没要写的内容"）。 */
export function hasSomethingToSave(form, {markReviewed = false} = {}) {
  return Object.keys(buildTranscriptPayload(form, {markReviewed})).length > 0;
}

/** 读数 → 给人看的几句（**服务原话优先**，界面不另编）。 */
export function describeReadout(readout) {
  if (!readout) return {ok: false, lines: ['还没有跑过转录。']};
  if (readout.ok === false) {
    const error = readout.error || {};
    return {ok: false, lines: [error.message || '触发转录失败',
                               error.hint ? `怎么办：${error.hint}` : ''].filter(Boolean)};
  }
  const result = readout.result || {};
  const lines = [];
  lines.push(result.parsed ? '读出来了，下面是草稿。' : `没读出来：${result.reason || '模型没给题面'}`);
  lines.push(`谁读的：${result.provider || '（没记）'} / ${result.model || '（没记）'}`);
  if (readout.ocr_engine && readout.ocr_engine !== 'unavailable') {
    lines.push(`本地 OCR：${readout.ocr_engine}（草稿 ${readout.ocr_draft_chars} 字）`);
  } else {
    lines.push('本地 OCR：不可用（这一趟只有视觉模型）');
  }
  if (Array.isArray(result.unreadable) && result.unreadable.length > 0) {
    lines.push(`看不清的地方：${result.unreadable.join('；')}`);
  }
  if (result.notes) lines.push(`模型备注：${result.notes}`);
  return {ok: true, lines};
}

/** 保存结果 → 一句人话。 */
export function describeSaved(status, envelope) {
  if (status === 200) {
    const changed = (envelope && envelope.data && envelope.data.changed) || [];
    return {ok: true, text: changed.length > 0 ? `已写进卡（改了 ${changed.length} 项）。`
                                               : '没有改动，什么都没写。'};
  }
  const error = (envelope && envelope.error) || {};
  return {ok: false, text: `${error.message || '保存失败'}${error.hint ? `；${error.hint}` : ''}`};
}
