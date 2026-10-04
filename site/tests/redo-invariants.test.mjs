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

import {checkQuestionImage} from './invariants.js';

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
