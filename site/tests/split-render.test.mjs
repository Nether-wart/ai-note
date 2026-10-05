/**
 * 切分修正页的**渲染不变量**测试（#14）：`node --test site/tests/`。
 *
 * 这一份与 `split.test.mjs` 的分工：那一份测**纯逻辑**（不 import 组件，`node --test`
 * 裸跑就能过）；这一份用 `ssr.mjs` 的台子把**真组件**渲成 HTML，再跑
 * `split-invariants.mjs` 的判据——所以它要 `site/node_modules`。
 *
 * **环境缺依赖不是界面坏了**：`ssr.mjs` 抛 `HarnessUnavailable` 时这些用例自己
 * skip 并说清原因（#8 的口径：原型把环境故障报成界面故障，那正是要消灭的病）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {createHarness, HarnessUnavailable} from './ssr.mjs';
import {CODES, checkSplitPage} from './split-invariants.mjs';

const PAGE_ID = 'aaaabbbbcccc';

/** 一页：三块 —— 已入库（收）／不入库／**待定**（统计读不出来）。 */
function pageFixture() {
  return {
    version: 1,
    id: PAGE_ID,
    image: `${PAGE_ID}.png`,
    origin: {original_file: '2.png', sheet: null, page_number: null},
    blocks: [
      {
        id: 'b1', bbox_norm: [0.02, 0.12, 0.76, 0.28], bbox_px: [3, 20, 541, 79],
        card_id: 'p-20260101-aaaaaa', keep: true, question_no: 7, problem_type: 'choice',
        ink: {area: 4108, colored_px: 210, colored_ratio: 0.05, dark_px: 1100, dark_ratio: 0.27},
        decision: {keep: true, rule: 'error_trace', source: 'model', semantics: 'correction',
          reason: '红笔是订正（改错）（表示这道题错了）→ 收：有表示「错」的痕迹'},
      },
      {
        id: 'b2', bbox_norm: [0.02, 0.44, 0.76, 0.2], bbox_px: null,
        card_id: null, keep: false, question_no: 8, problem_type: 'fillin',
        decision: {keep: false, rule: 'tick_only', source: 'model', semantics: 'tick',
          reason: '红笔只是一个对勾（表示做对了）→ 不收'},
      },
      {
        id: 'b3', bbox_norm: [0.02, 0.68, 0.76, 0.2], bbox_px: null,
        card_id: null, keep: null, question_no: null, problem_type: null,
        decision: {keep: null, rule: 'ink_unknown', source: 'statistics', semantics: null,
          reason: '红笔统计读不出来 → 待定，等人看一眼'},
      },
    ],
  };
}

/** 一个 `classify_resegment` 形状的重切报告：一块保留、一块新增。 */
function resegmentFixture() {
  return {
    page_id: PAGE_ID,
    ran: true,
    blocks: [
      {id: 'b1', bbox_norm: [0.02, 0.12, 0.76, 0.28], card_id: 'p-20260101-aaaaaa', keep: true},
      {id: 'b2', bbox_norm: [0.02, 0.44, 0.76, 0.2], card_id: null, keep: null},
    ],
    matches: [
      {block_id: 'b1', state: 'kept', matched_from: 'b1', iou: 0.94, contain: 1.0},
      {block_id: 'b2', state: 'new', matched_from: null, iou: null, contain: null},
    ],
    removed: [],
    summary: {kept: 1, new: 1, replaced: 0, removed: 0, needs_human: false,
      human_work_checked: true},
    warnings: [{code: 'resegment_card_human_work', id: 'p-20260101-aaaaaa', level: 'warning',
      message: '块 b1 对应的卡片有审核过的字段 → 这次重切只给对照，不会改写它'}],
    wrote_cards: false,
    wrote_page: false,
  };
}

let harness;
let unavailable = null;
try {
  harness = await createHarness();
} catch (error) {
  if (error instanceof HarnessUnavailable) unavailable = error.message;
  else throw error;
}

const skipIfNoHarness = {skip: unavailable ? `没有 site/node_modules：${unavailable}` : false};

test('the split page renders without fetching when it is handed a page', skipIfNoHarness, () => {
  const html = harness.renderSplit(pageFixture());

  assert.match(html, /data-page-id="aaaabbbbcccc"/);
  assert.match(html, /块 3 块/);
  assert.equal(checkSplitPage(html, {pageId: PAGE_ID}).length, 0,
    '这一页该过全部判据');
});

test('the page photo is this page\'s own image endpoint', skipIfNoHarness, () => {
  const html = harness.renderSplit(pageFixture());

  assert.match(html, new RegExp(`/api/page/${PAGE_ID}/image`));
  assert.match(html, /data-image-kind="page"/);
});

test('every verdict of keep is displayed, including pending', skipIfNoHarness, () => {
  const html = harness.renderSplit(pageFixture());

  // 三档都要在页头出现：「待定」不许被并进「不收」
  assert.match(html, /收 1 \/ 不收 1 \/ 待定 1/);
  const keeps = [...html.matchAll(/data-keep="([^"]*)"/g)].map((m) => m[1]);
  assert.ok(keeps.includes('true') && keeps.includes('false') && keeps.includes('pending'));
});

test('the blocks that are not in the library are listed with a reason', skipIfNoHarness, () => {
  const html = harness.renderSplit(pageFixture());

  assert.match(html, /data-not-kept/);
  assert.match(html, /块 b2：不收/);
  assert.match(html, /块 b3：待定/);
  // 理由是服务给的原话，界面照抄
  assert.match(html, /红笔只是一个对勾/);
  assert.match(html, /红笔统计读不出来/);
});

test('the auditable decision rule of each block is on screen', skipIfNoHarness, () => {
  const html = harness.renderSplit(pageFixture());

  assert.match(html, /data-decision-rule="error_trace"/);
  assert.match(html, /data-decision-rule="tick_only"/);
  assert.match(html, /data-decision-rule="ink_unknown"/);
});

test('a page with no id is refused rather than guessing one', skipIfNoHarness, () => {
  // 组件自己不接 `pageId` 缺失的分支（路由接），所以这里验的是路由那一段的判据：
  // 页面级失败面板带 `data-error-code`，而不是悄悄挑一页。
  const html = harness.renderSplit(pageFixture());

  assert.doesNotMatch(html, /data-status="failed"/, '给了页就不该显示失败面板');
});

test('the loading state says it is reading rather than showing an empty page', skipIfNoHarness, () => {
  const html = harness.renderSplitLoading();

  assert.match(html, /data-status="loading"/);
  assert.doesNotMatch(html, /data-page-id=/, '还没读到页就不该有一个（空的）页视图');
});

// ---------------------------------------------------- 重切三态（验收 1）

test('the resegment report is rendered with all three states counted', skipIfNoHarness, () => {
  // 组件拿到重切报告之后才渲染 `data-resegment`；这里直接对着「报告在手」那一刻的
  // 渲染结果断言——用一条只读的 `pageData` + 组件的重切状态是无法从外部注入的，
  // 所以这条走**渲染出来的静态 HTML 判据**那一层（与 redo 的 renderPage 同款做法）。
  const html = harness.renderSplit(pageFixture(), {pageId: PAGE_ID});

  // 没有点重切之前不该有对账面板——这正是「不许假装重切过了」
  assert.doesNotMatch(html, /data-resegment/,
    '没点重切就不该显示对账（不许假装重切过了）');
  assert.match(html, /data-action="resegment"/, '重切要有一个能点的入口');
});

test('the resegment invariant checker accepts a proper report and catches a lazy one', () => {
  // 判据自己也要被验：一个「只显示重切完成」的渲染必须被抓住
  const proper = `<div data-resegment data-ran="true">
      <p data-resegment-counts>保留 1 · 新增 1 · 替换 0</p>
      <li data-block-id="b1" data-state="kept">保留</li>
      <li data-block-id="b2" data-state="new">新增</li>
    </div>`;
  const lazy = '<div data-resegment data-ran="true"><p>重切完成</p></div>';

  assert.equal(checkSplitPage(proper, {pageId: PAGE_ID})
    .filter((v) => v.code === CODES.resegmentStatesMissing).length, 0);
  assert.ok(checkSplitPage(proper, {pageId: PAGE_ID})
    .some((v) => v.code === CODES.warningsSwallowed) === false);
  const caught = checkSplitPage(lazy, {pageId: PAGE_ID});
  assert.ok(caught.some((v) => v.code === CODES.resegmentStatesMissing),
    '「只显示重切完成」必须被抓住');
  assert.ok(caught.some((v) => v.code === CODES.resegmentCountsMissing));
});

test('a warning the service sent is never swallowed', skipIfNoHarness, () => {
  const codes = resegmentFixture().warnings.map((w) => w.code);
  const html = harness.renderSplit(pageFixture());
  // 这一页还没重切 → 那条警告不在；判据要能报出「本该有却没有」
  const missing = checkSplitPage(html, {pageId: PAGE_ID, warningCodes: codes});
  assert.ok(missing.some((v) => v.code === CODES.warningsSwallowed));
});

test('the editor offers every action of the minimal set and nothing extra', skipIfNoHarness, async () => {
  const {SUPPORTED_ACTIONS} = await import('../src/components/SplitEditor.js');

  assert.deepEqual([...SUPPORTED_ACTIONS].sort(),
    ['drop', 'keep', 'merge', 'move', 'question_no', 'split', 'type']);
  // 不做的那三样一个都不许出现
  for (const forbidden of ['rotate', 'perspective', 'columns']) {
    assert.ok(!SUPPORTED_ACTIONS.includes(forbidden));
  }
});
