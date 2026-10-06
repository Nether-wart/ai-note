/**
 * 判定结果面板的**渲染**测试（R7）：`node --test site/tests/`。
 *
 * 修前的失败模式是「两句话同时出现」：判错时面板一边印服务给的「判错：清零回池」，
 * 一边印界面自己补的「只热身，不计入掌握」——而「热身」只限**判对但仍在冷却期**
 * （spec #1 故事 9），判错要的是**清零回池**（故事 10）。纯逻辑测 `creditLine`
 * 只能证明它返回什么；这条回归的真实形状是**面板上有什么、没有什么**，
 * 所以这里把真组件渲成 HTML 再断言。
 *
 * **环境缺依赖不是界面坏了**：`ssr.mjs` 抛 `HarnessUnavailable` 时这些用例自己
 * skip 并说清原因（#8 的口径）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {attemptResult} from '../src/lib/redo.js';
import {createHarness, HarnessUnavailable} from './ssr.mjs';

/** 服务的一封回应（契约 §10.1）：`verdict` 与 `mastery` 都由服务给。 */
function response(verdict, {credited = false, streak = 0} = {}) {
  const note =
    verdict === 'wrong'
      ? '判错：清零回池（毕业若存在则取消）'
      : credited
        ? `判对且脱离冷却：连续正确 ${streak}/2`
        : '判对但仍在冷却期（距上次重做 3 天）：这次只热身';
  return {
    attempt: {at: '2026-10-04T09:39:57+00:00', channel: 'screen', verdict,
      source: 'auto', confidence: 0.95, provider: 'deepseek', model: 'deepseek-flash',
      note},
    mastery: {state: 'in_pool', streak, credited, cooling: !credited && verdict === 'correct',
      note},
    run_id: null,
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

test('判对且计入 → 面板只说「已计入」，不说热身', skipIfNoHarness, () => {
  const html = harness.renderResult(attemptResult(response('correct', {credited: true, streak: 1})));

  assert.match(html, /data-credit="credited"/);
  assert.match(html, /已计入 1\/2/);
  assert.doesNotMatch(html, /只热身/);
});

test('判对但仍在冷却期 → 面板只说「只热身，不计入掌握」', skipIfNoHarness, () => {
  const html = harness.renderResult(attemptResult(response('correct')));

  assert.match(html, /data-credit="warmup"/);
  assert.match(html, /只热身，不计入掌握/);
  assert.doesNotMatch(html, /清零回池/);
});

test('判错 → 面板只说「清零回池」，**不再**并排说热身', skipIfNoHarness, () => {
  const html = harness.renderResult(attemptResult(response('wrong')));

  assert.match(html, /data-credit="reset"/);
  assert.match(html, /判错：清零回池/);
  assert.doesNotMatch(html, /只热身/, '判错不是热身：两句并排会让人以为判错没有代价');
  assert.doesNotMatch(html, /既不推进也不清零/);
});
