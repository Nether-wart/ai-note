/**
 * 题卡属性编辑表单的纯逻辑（`PATCH /api/problem/<id>`，契约 §10.7）。
 *
 * 组件那一半在 `components/ProblemEdit.js`；这里只管**提交什么**与**怎么校验**。
 *
 * 两条最要紧的：
 *
 * 1. **只提交改动过的字段**。服务端虽然"值没变就不写盘"，但少发几个键有两个好处：
 *    回执里的 `changed` 就是用户真正动过的那几项，不会因为"整份回显再整份提交"
 *    而出现"看着改了一堆、其实什么都没改"。这也是那个设置页踩过的坑的同一条。
 * 2. **还没验的东西不假装验过**。错因词表这份界面拿不到（`/api/index` 只给科目与大纲），
 *    所以这里**不猜**词表内容：只验形状，取值留给服务端，它拒了就把**它说的那句话**
 *    原样显示（它连可选值都带回来了）。
 */

/** 可改字段的闭集（与服务端的 `EDITABLE` 一致；顺序就是界面里从上到下的顺序）。 */
export const EDITABLE_FIELDS = ['subject', 'topics', 'error_causes', 'review'];

export const REVIEW_STATES = [
  {value: 'unreviewed', label: '未审核'},
  {value: 'reviewed', label: '已审核'},
];

/** `review` 在卡上是对象、在详情读数是字符串——两种都认，别让界面依赖是哪一种。 */
export function reviewStatus(review) {
  if (typeof review === 'string') {
    return review === 'reviewed' ? 'reviewed' : 'unreviewed';
  }
  return review?.status === 'reviewed' ? 'reviewed' : 'unreviewed';
}

/** 题卡（或详情读数）→ 表单状态。缺的字段按"还没有"处理，不编默认值。 */
export function formFromCard(card) {
  return {
    subject: card?.subject ?? null,
    topics: [...(card?.topics || [])],
    error_causes: [...(card?.error_causes || [])],
    review: reviewStatus(card?.review),
  };
}

function sameList(left, right) {
  if (left.length !== right.length) return false;
  return left.every((value, index) => value === right[index]);
}

/** 表单 ＋ 原始卡 → `PATCH` 的请求体（**只放改过的字段**；没改就是 `{}`）。 */
export function buildPatch(form, card) {
  const original = formFromCard(card);
  const patch = {};
  if ((form.subject ?? null) !== original.subject) patch.subject = form.subject ?? null;
  if (!sameList(form.topics, original.topics)) patch.topics = form.topics;
  if (!sameList(form.error_causes, original.error_causes)) patch.error_causes = form.error_causes;
  if (form.review !== original.review) patch.review = form.review;
  return patch;
}

/** 加一项（去重、保序、去空白）。空串不进列表——那是"点了加但没填"。 */
export function addItem(list, value) {
  const item = String(value ?? '').trim();
  if (!item || list.includes(item)) return list;
  return [...list, item];
}

export function removeItem(list, value) {
  return list.filter((item) => item !== value);
}

/**
 * 提交前的自检。**服务端仍会再验一遍**（那一道才是权威）；这里只说形状上的问题。
 */
export function validateEdit(form, {subjects = null} = {}) {
  const problems = [];
  if (subjects && form.subject && !subjects.includes(form.subject)) {
    problems.push(`科目 ${form.subject} 不在受控词表里`);
  }
  for (const [field, label] of [['topics', '考点'], ['error_causes', '错因']]) {
    for (const item of form[field]) {
      if (!item.trim()) problems.push(`${label}里有一项是空的`);
    }
  }
  return problems;
}

/** 保存结果 → 给人看的话。**成功与失败都用服务说的那句**，界面不另编一套。 */
export function describeSave(status, envelope) {
  if (status === 200) {
    const changed = envelope?.data?.changed || [];
    return {
      ok: true,
      text: changed.length > 0 ? `已保存（改了 ${changed.length} 项）。` : '没有改动，什么都没写。',
      changed,
    };
  }
  const error = envelope?.error || {};
  const allowed = error.details?.allowed;
  const tail = Array.isArray(allowed) && allowed.length > 0 ? `（可选：${allowed.join('、')}）` : '';
  return {ok: false, text: `${error.message || '保存失败'}${tail}`, changed: []};
}
