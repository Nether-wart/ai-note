/**
 * 移动端那几条**量出来过**的毛病，钉成能在源码层红的判据（`node --test site/tests/`）。
 *
 * 为什么住在源码层：这几条都是 CSS 的属性值——没有 React 组件可渲，也不值得为它们
 * 起一个浏览器。而它们**真的咬过人**（读数见 `site/README.md` 的「移动端」一节）：
 * 360px 的手机上整页被一道分段函数撑到 421px、索引栏 1719px 高把正文顶到屏幕以外、
 * 输入框 13.3px 触发 iOS 聚焦放大、照片上的 `touch-action: none` 让手指划不动页面。
 *
 * 与 `bundle-safe.test.mjs` 同一条纪律（`docs/agents/pitfalls.md` 第 2 条）：
 * **先剥注释再扫**——解释这条规矩的注释里必然出现那个坏写法；并且每条模式判据都配一条
 * 「对坏写法会响」的自证，否则它可能是一条永远绿的判据。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const CSS = new URL('../src/css/custom.css', import.meta.url);
const SIDEBAR = new URL('../src/components/Sidebar.js', import.meta.url);

/** 剥注释：这一份 CSS 只有 `/* … *\/` 一种注释。 */
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * 把 CSS 拆成 `{选择器, 声明原文}`。
 *
 * `[^{}]+` 跨不过 `}`，所以媒体查询里的规则也能被单独取出来，选择器不会被
 * `@media` 那一段污染（不需要写一个完整的 CSS 解析器）。
 */
function rules(css) {
  return Array.from(stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g), (match) => ({
    selector: match[1].trim().replace(/\s+/g, ' '),
    body: match[2],
  }));
}

/** 声明原文 → `{属性: 值}`。这份文件里没有 `;` 出现在值里的情况。 */
function declarations(body) {
  const out = {};
  for (const part of body.split(';')) {
    const at = part.indexOf(':');
    if (at === -1) continue;
    out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return out;
}

/** 长度 → 像素（`1rem` 按 16px）；认不出给 `null`。 */
function px(value, root = 16) {
  const match = /^([\d.]+)(px|rem|em|ch)?$/.exec(String(value ?? '').trim());
  if (!match) return null;
  return match[2] === 'px' || match[2] === undefined ? Number(match[1]) : Number(match[1]) * root;
}

const CSS_TEXT = readFileSync(CSS, 'utf8');

/** 裸的 `minmax(<长度>, …)`：第一个参数没有 `min(…, 100%)` 兜底。 */
const BARE_MINMAX = /minmax\(\s*(?!min\()[\d.]+(?:rem|px|em|ch)\s*,/;

/** 所有写了 `touch-action: none` 的选择器（一条规则里逗号分开的逐个算）。 */
function touchNoneSelectors(css) {
  return rules(css)
    .filter((rule) => /touch-action:\s*none/.test(rule.body))
    .flatMap((rule) => rule.selector.split(',').map((selector) => selector.trim()));
}

test('栅格不许写裸的 minmax(<长度>, …)：比它窄的屏幕上会把页面撑出横向滚动', () => {
  const offenders = [];
  for (const rule of rules(CSS_TEXT)) {
    for (const value of Object.values(declarations(rule.body))) {
      for (const hit of value.matchAll(new RegExp(BARE_MINMAX, 'g'))) {
        offenders.push(`${rule.selector}: ${hit[0]}…`);
      }
    }
  }
  assert.deepEqual(offenders, [], `这几处要写成 minmax(min(<长度>, 100%), 1fr)：\n  ${offenders.join('\n  ')}`);
});

test('栅格那条判据对坏写法会响', () => {
  assert.ok(BARE_MINMAX.test('minmax(20rem, 1fr)'));
  assert.ok(!BARE_MINMAX.test('minmax(min(20rem, 100%), 1fr)'));
});

test('touch-action: none 只许出现在确实要吃掉手势的那几个选择器上', () => {
  // 收手势的只有三种：拖块框、画框模式（`--draw` 本身与它里面的照片）。
  // 挂在 `.ai-note-page-photo` 上时，手机上手指按在照片上就滚不动页面。
  const allowed = new Set([
    '.ai-note-block-box',
    '.ai-note-page-photo--draw',
    '.ai-note-page-photo--draw img',
  ]);
  const offenders = touchNoneSelectors(CSS_TEXT).filter((selector) => !allowed.has(selector));
  assert.deepEqual(offenders, [], `这几个选择器不许吃掉滚动手势：\n  ${offenders.join('\n  ')}`);
});

test('touch-action 那条判据对坏写法会响', () => {
  assert.deepEqual(
    touchNoneSelectors('.ai-note-page-photo { touch-action: none; }'),
    ['.ai-note-page-photo'],
  );
  assert.deepEqual(
    touchNoneSelectors('.ai-note-page-photo { touch-action: pan-y; }'),
    [],
  );
});

test('窄屏上表单控件字号 ≥ 16px（iOS 聚焦会自动放大整页）', () => {
  const sizes = [];
  for (const rule of rules(CSS_TEXT)) {
    if (!/input|select|textarea/.test(rule.selector)) continue;
    const size = px(declarations(rule.body)['font-size']);
    if (size !== null) sizes.push({selector: rule.selector, size});
  }
  assert.ok(sizes.length > 0, '没有任何规则给表单控件定字号——这条判据会永远绿');
  const smallest = sizes.reduce((min, item) => (item.size < min.size ? item : min));
  assert.ok(smallest.size >= 16, `${smallest.selector} 的字号是 ${smallest.size}px，小于 16px`);
});

test('科目索引栏在窄屏默认收起，而且真的有一个开关', () => {
  const listRules = rules(CSS_TEXT).filter((rule) => rule.selector === '.ai-note-sidebar__list');
  assert.ok(
    listRules.some((rule) => declarations(rule.body).display === 'none'),
    '窄屏没有把索引栏收起来：9 个科目 × 5 条链接在手机上高过一整屏正文',
  );
  const openRules = rules(CSS_TEXT).filter(
    (rule) => rule.selector === ".ai-note-sidebar[data-mobile-open='true'] .ai-note-sidebar__list",
  );
  assert.ok(
    openRules.some((rule) => declarations(rule.body).display === 'block'),
    '收起来之后没有展开它的规则',
  );
  const source = readFileSync(SIDEBAR, 'utf8');
  assert.match(source, /data-action="toggle-sidebar"/, 'Sidebar 里没有那个开关');
  assert.match(source, /aria-expanded=\{mobileOpen\}/, '开关没有把展开状态说给读屏器');
});
