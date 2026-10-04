/**
 * 屏幕重做页的界面不变量（#8）——**报警能力**的测试。
 *
 * 这份文件只喂 `invariants.js` 一段 HTML，所以它是**判据本身**的测试：
 * 每条不变量都要有两个方向——**好输入不报警**，**坏输入必须报警**。
 * 「只测好输入不报警」等于没测：这个工单的全部价值是**它会不会响**。
 *
 * 真实渲染（React SSR 出来的 HTML）的检查在 `selftest.mjs` 里，用的是同一个判据。
 *
 * 判据对着**真实 HTML** 写，不是 CSS 选择器写法：原型在这里栽过——它的第一版自检
 * 查的是 `.gates`，而页面里写的是 `class="gates"`，于是报了 20 条全是误报
 * （`docs/acceptance-log.md:253`、`proto/server.py:983`）。下面这些字符串就是
 * SSR 真的会吐出来的形状（见 `selftest.mjs` 的真实渲染）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {checkAnswerBox, checkQueue, checkQuestionImage} from './invariants.mjs';

// ---------------------------------------------------------------- 夹具

const API = 'http://127.0.0.1:8765';
const pid = 'p-a';
const CLEAN = `${API}/api/problem/${pid}/image/clean`;
const ORIGINAL = `${API}/api/problem/${pid}/image/original`;
const MASK = `${API}/api/problem/${pid}/image/mask`;
const PAGE_PHOTO = `${API}/api/page/41c86bcfc007/image`;

/** React 19 的 `renderToStaticMarkup` 会给 `<img>` 额外吐一个 preload link。 */
const cleanQuestionHtml = (extra = '') =>
  `<link rel="preload" as="image" href="${CLEAN}"/>` +
  `<article class="ai-note-card" data-current-pid="${pid}" data-answer-mode="choice">` +
  `<img src="${CLEAN}" alt="${pid} 擦除手写后的题面" data-image-kind="clean"/>` +
  extra +
  `</article>`;

const codes = (violations) => violations.map((v) => v.code);

// ------------------------------------------- 不变量 1：题面只能是擦除后的图

test('不变量 1 好输入：题面是擦除图 → 一条都不报', () => {
  assert.deepEqual(codes(checkQuestionImage({html: cleanQuestionHtml(), pid, apiBase: API})), []);
});

test('不变量 1 坏输入：题面被换成原图（含手写与订正）→ 必须报警', () => {
  const html = cleanQuestionHtml().replace(CLEAN, ORIGINAL);
  const violations = checkQuestionImage({html, pid, apiBase: API});
  assert.deepEqual(codes(violations), ['question_image_not_clean']);
  assert.match(violations[0].message, /原图|擦除/);
  assert.equal(violations[0].details.found, ORIGINAL);
});

test('不变量 1 坏输入：声明说 clean、src 却指向原图 → 仍要报警（判据对真实 URL，不信声明）', () => {
  const html = cleanQuestionHtml().replace(
    `<img src="${CLEAN}" alt="${pid} 擦除手写后的题面" data-image-kind="clean"/>`,
    `<img src="${ORIGINAL}" alt="${pid} 擦除手写后的题面" data-image-kind="clean"/>`,
  );
  assert.deepEqual(codes(checkQuestionImage({html, pid, apiBase: API})), ['question_image_not_clean']);
});

test('不变量 1 坏输入：整页原图（source.page_image）出现在重做页 → 必须报警', () => {
  const html = cleanQuestionHtml(`<img src="${PAGE_PHOTO}" data-image-kind="clean"/>`);
  assert.deepEqual(codes(checkQuestionImage({html, pid, apiBase: API})), ['question_image_not_clean']);
});

test('不变量 1 坏输入：手写掩膜图（cleanmask）也不许出现在题面区', () => {
  const html = cleanQuestionHtml().replace(CLEAN, MASK);
  assert.deepEqual(codes(checkQuestionImage({html, pid, apiBase: API})), ['question_image_not_clean']);
});

test('不变量 1 坏输入：题面区一张图都没有（图没渲染出来）→ 也要报警，不是"没图就算了"', () => {
  const html = `<article class="ai-note-card" data-current-pid="${pid}"><div data-transcript="true">题干</div></article>`;
  assert.deepEqual(codes(checkQuestionImage({html, pid, apiBase: API})), ['question_image_missing']);
});

test('不变量 1 坏输入：kind 属性说自己是 clean、URL 对得上，但标记与内容不符 → 报警', () => {
  const html = cleanQuestionHtml().replace('data-image-kind="clean"', 'data-image-kind="original"');
  const violations = checkQuestionImage({html, pid, apiBase: API});
  assert.ok(codes(violations).includes('question_image_kind_mismatch'), JSON.stringify(violations));
});

// ------------------------------------------- 不变量 2：解答题不得给出作答框

const CHOICE_PROBLEM = {
  id: pid,
  type: 'choice',
  options: [{label: 'A', text: '甲'}, {label: 'B', text: '乙'}],
  auto_judge: {eligible: true, reason: null, reason_text: null},
};
const FILLIN_PROBLEM = {
  id: pid,
  type: 'fillin',
  options: [],
  auto_judge: {eligible: true, reason: null, reason_text: null},
};
const SOLUTION_PROBLEM = {
  id: pid,
  type: 'solution',
  options: [],
  auto_judge: {
    eligible: false,
    reason: 'solution_type',
    reason_text: '解答题只能人工确认：过程题在屏幕上敲不出过程',
  },
};

const card = (mode, body) =>
  `<article class="ai-note-card" data-current-pid="${pid}" data-answer-mode="${mode}">` +
  `<img src="${CLEAN}" data-image-kind="clean"/>${body}</article>`;

const CHOICE_BODY =
  '<form data-answer-form="choice"><fieldset data-answer-input="choice">' +
  '<label><input type="radio" name="answer" value="A"/> A. 甲</label>' +
  '<label><input type="radio" name="answer" value="B"/> B. 乙</label></fieldset>' +
  '<button type="submit" data-submit-attempt="true">提交作答</button></form>';
const FILLIN_BOX_BODY =
  '<form data-answer-form="fillin"><label data-answer-input="fillin">填空：' +
  '<input type="text" autocomplete="off"/></label>' +
  '<button type="submit" data-submit-attempt="true">提交作答</button></form>';
const BLOCKED_BODY =
  '<div class="ai-note-banner" role="alert" data-answer-blocked="solution_type">' +
  '<strong>这道题不给作答框。</strong><p>解答题只能人工确认：过程题在屏幕上敲不出过程</p></div>';

test('不变量 2 好输入：选择题给选项、填空题给一个空、解答题不给框 → 一条都不报', () => {
  assert.deepEqual(codes(checkAnswerBox({html: card('choice', CHOICE_BODY), problem: CHOICE_PROBLEM})), []);
  assert.deepEqual(codes(checkAnswerBox({html: card('fillin', FILLIN_BOX_BODY), problem: FILLIN_PROBLEM})), []);
  assert.deepEqual(codes(checkAnswerBox({html: card('none', BLOCKED_BODY), problem: SOLUTION_PROBLEM})), []);
});

test('不变量 2 坏输入：解答题出现了作答框 → 必须报警', () => {
  const html = card('fillin', FILLIN_BOX_BODY);
  const violations = checkAnswerBox({html, problem: SOLUTION_PROBLEM});
  assert.ok(codes(violations).includes('solution_has_answer_box'), JSON.stringify(violations));
});

test('不变量 2 坏输入：解答题的框只要有一个 <input> 就算（哪怕没有 form 包着）→ 报警', () => {
  const html = card('none', '<p>过程：<input type="text" autocomplete="off"/></p>');
  assert.ok(codes(checkAnswerBox({html, problem: SOLUTION_PROBLEM})).includes('solution_has_answer_box'));
});

test('不变量 2 坏输入：解答题给了 <textarea> 也算作答框 → 报警', () => {
  const html = card('none', '<textarea name="answer"></textarea>');
  assert.ok(codes(checkAnswerBox({html, problem: SOLUTION_PROBLEM})).includes('solution_has_answer_box'));
});

test('不变量 2 坏输入：未审核／无标准答案的题有作答框 → 报警（理由不是解答题也要报）', () => {
  const unreviewed = {...FILLIN_PROBLEM, auto_judge: {eligible: false, reason: 'unreviewed', reason_text: '还没审核'}};
  const violations = checkAnswerBox({html: card('fillin', FILLIN_BOX_BODY), problem: unreviewed});
  assert.ok(codes(violations).includes('blocked_question_has_answer_box'), JSON.stringify(violations));
});

test('不变量 2 坏输入：该有框的题没有框（题面渲染出来了、作答区丢了）→ 报警', () => {
  assert.ok(codes(checkAnswerBox({html: card('choice', '<div data-transcript="true">题干</div>'), problem: CHOICE_PROBLEM}))
    .includes('answer_box_missing'));
});

test('不变量 2 坏输入：选择题的选项丢了（题型枚举静默退回默认值的同族）→ 报警', () => {
  const html = card('choice', '<form data-answer-form="choice"><button type="submit">提交</button></form>');
  assert.ok(codes(checkAnswerBox({html, problem: CHOICE_PROBLEM})).includes('choice_options_missing'));
});

test('不变量 2 坏输入：data-answer-mode 与判据读数不符 → 报警（组件宣称的和做的不一样）', () => {
  const violations = checkAnswerBox({html: card('choice', FILLIN_BOX_BODY), problem: SOLUTION_PROBLEM});
  assert.ok(codes(violations).includes('answer_mode_mismatch'), JSON.stringify(violations));
});

// --------------------------------- 不变量 3：越界必须失败，不回退到别的题

const QUEUE_PROBLEMS = [
  {id: 'p-a', type: 'choice', options: [{label: 'A', text: '甲'}]},
  {id: 'p-b', type: 'choice', options: [{label: 'A', text: '甲'}]},
];

const renderedCard = (renderedPid) => card('choice', CHOICE_BODY).replace(`data-current-pid="${pid}"`, `data-current-pid="${renderedPid}"`);
const failurePanel = (code) =>
  `<div class="ai-note-banner" role="alert" data-status="failed" data-error-code="${code}">` +
  `<p data-error-message="true">…</p></div>`;

test('不变量 3 好输入：队列在范围内、渲染的是第 i 道 → 一条都不报', () => {
  const html = renderedCard('p-b');
  assert.deepEqual(codes(checkQueue({html, search: '?queue=p-a,p-b&i=1', problems: QUEUE_PROBLEMS})), []);
});

test('不变量 3 好输入：越界时渲染成带 data-error-code 的失败面板 → 一条都不报', () => {
  const html = failurePanel('index_out_of_range');
  assert.deepEqual(codes(checkQueue({html, search: '?queue=p-a,p-b&i=9', problems: QUEUE_PROBLEMS})), []);
});

test('不变量 3 坏输入：越界却渲染出了第一题（悄悄落到别的题上）→ 必须报警', () => {
  const html = renderedCard('p-a');
  const violations = checkQueue({html, search: '?queue=p-a,p-b&i=9', problems: QUEUE_PROBLEMS});
  assert.ok(codes(violations).includes('fell_back_to_the_first_problem'), JSON.stringify(violations));
});

test('不变量 3 坏输入：越界但页面上什么失败都没有（静默跳过）→ 必须报警', () => {
  const violations = checkQueue({html: '<div data-redo-page="true"></div>', search: '?queue=p-a,p-b&i=9', problems: QUEUE_PROBLEMS});
  assert.deepEqual(codes(violations), ['queue_failure_not_rendered']);
});

test('不变量 3 坏输入：失败面板报的是另一个码（越界说成队列为空）→ 报警', () => {
  const violations = checkQueue({html: failurePanel('queue_empty'), search: '?queue=p-a,p-b&i=9', problems: QUEUE_PROBLEMS});
  assert.ok(codes(violations).includes('queue_failure_wrong_code'), JSON.stringify(violations));
});

test('不变量 3 坏输入：在范围内却渲染了第一题（第 i 道被换掉）→ 报警', () => {
  const violations = checkQueue({html: renderedCard('p-a'), search: '?queue=p-a,p-b&i=1', problems: QUEUE_PROBLEMS});
  assert.ok(codes(violations).includes('fell_back_to_the_first_problem'), JSON.stringify(violations));
});

test('不变量 3 坏输入：在范围内却一道题都没渲染出来 → 报警', () => {
  const violations = checkQueue({html: '<div data-redo-page="true"></div>', search: '?queue=p-a,p-b&i=1', problems: QUEUE_PROBLEMS});
  assert.deepEqual(codes(violations), ['expected_problem_not_rendered']);
});

test('不变量 3 坏输入：没出错却摆了一个失败面板 → 报警（不许吓人）', () => {
  const violations = checkQueue({html: renderedCard('p-b') + failurePanel('index_out_of_range'),
    search: '?queue=p-a,p-b&i=1', problems: QUEUE_PROBLEMS});
  assert.ok(codes(violations).includes('spurious_queue_error'), JSON.stringify(violations));
});

test('不变量 3 坏输入：队列里的题在索引里找不到却跳过它继续做题（原型 :320-321 那种过滤）→ 报警', () => {
  const html = renderedCard('p-a');
  const violations = checkQueue({html, search: '?queue=p-a,p-gone&i=1', problems: QUEUE_PROBLEMS});
  assert.ok(codes(violations).includes('other_problem_rendered'), JSON.stringify(violations));
});

test('不变量 3 好输入：队列里的题在索引里找不到，但渲染成 problem_not_in_index 面板 → 一条都不报', () => {
  const html = failurePanel('problem_not_in_index');
  assert.deepEqual(codes(checkQueue({html, search: '?queue=p-a,p-gone&i=1', problems: QUEUE_PROBLEMS})), []);
});

test('不变量 3 坏输入：队列解析失败（没有 queue）却没有失败面板 → 报警', () => {
  const violations = checkQueue({html: renderedCard('p-a'), search: '', problems: QUEUE_PROBLEMS});
  assert.deepEqual(codes(violations), ['queue_failure_not_rendered']);
});
