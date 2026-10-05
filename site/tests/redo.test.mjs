/**
 * 重做页（#7）纯逻辑的测试：`node --test site/tests/`。
 *
 * 这些是**界面里唯一能被离线断言的那部分**：队列在 URL 里怎么解析、越界怎么**
 * 明确失败**、输入怎么随题型分化、题面图怎么取、提交给服务的形状。
 *
 * 断言的都是**外部行为**（输入 → 读数），不碰内部变量；夹具是手写的、不来自
 * 真实数据目录（工单 #1 第 35 条：真实题卡不被测试碰）。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  answerMode,
  attemptResult,
  buildRedoUrl,
  creditLine,
  parseRedoQuery,
  problemImage,
  queueForProblems,
  redoHeader,
  removeAt,
  resolveQueueItem,
  screenPayload,
  submitScreenAnswer,
} from '../src/lib/redo.js';

// ---------------------------------------------------------------- 夹具

function problem(overrides = {}) {
  return {
    id: 'p-a',
    type: 'choice',
    transcript: '1. 一道题',
    options: [{label: 'A', text: '甲'}, {label: 'B', text: '乙'}],
    images: {
      original: '/api/problem/p-a/image/original',
      clean: '/api/problem/p-a/image/clean',
      mask: '/api/problem/p-a/image/mask',
    },
    auto_judge: {eligible: true, reason: null, reason_text: null},
    screen_redo: {ready: true, blockers: [], blocker_text: []},
    in_default_list: true,
    cooling: false,
    graduated: false,
    warnings: [],
    ...overrides,
  };
}

const SCREEN_REDO = {
  default_basis: 'in_default_list',
  bases: {
    in_default_list: {
      basis_text: '默认打印清单（未毕业且已脱离冷却）',
      ready: 1,
      blocked_total: 2,
      not_auto_judgeable: {
        count: 2,
        by_reason: [
          {reason: 'solution_type', reason_text: '解答题只能人工确认：过程题在屏幕上敲不出过程', count: 1, ids: ['p-sol']},
          {reason: 'unreviewed', reason_text: '这道题还没审核 → 标准答案还不可信，不参与自动判定', count: 1, ids: ['p-unrev']},
        ],
      },
      no_clean_image: {
        count: 1,
        ids: ['p-noclean'],
        message: '另有 1 道因缺少擦除手写后的题面图不能进屏幕重做',
      },
    },
    including_cooling: {
      basis_text: '未毕业（含冷却中，「显示冷却中的题」勾上时）',
      ready: 3,
      blocked_total: 2,
      not_auto_judgeable: {count: 2, by_reason: []},
      no_clean_image: {count: 1, ids: ['p-noclean'], message: '另有 1 道因缺少擦除手写后的题面图不能进屏幕重做'},
    },
  },
};

// ------------------------------------------------- 验收 1：队列在 URL 里

test('parseRedoQuery：正常的 queue/i 解析成队列与索引', () => {
  const parsed = parseRedoQuery('?queue=p-a,p-b&i=1');
  assert.equal(parsed.ok, true);
  assert.deepEqual(parsed.queue, ['p-a', 'p-b']);
  assert.equal(parsed.i, 1);
  assert.equal(parsed.basis, null);
});

test('parseRedoQuery：没有 queue 就明确失败，绝不悄悄落到第一题', () => {
  for (const search of ['', '?i=0', '?foo=bar']) {
    const parsed = parseRedoQuery(search);
    assert.equal(parsed.ok, false, `${search} 必须失败`);
    assert.equal(parsed.code, 'queue_missing');
    assert.ok(parsed.message.length > 0);
    assert.equal(parsed.details.param, 'queue');
  }
});

test('parseRedoQuery：queue 是空的（`queue=`）也明确失败', () => {
  const parsed = parseRedoQuery('?queue=&i=0');
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, 'queue_empty');
});

test('parseRedoQuery：缺 i 明确失败，缺省到 0 就是「悄悄落到第一题」', () => {
  const parsed = parseRedoQuery('?queue=p-a,p-b');
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, 'index_missing');
});

test('parseRedoQuery：i 非法（非整数／负数／小数／空）明确失败', () => {
  for (const raw of ['abc', '-1', '1.5', '', ' 1', '0x1']) {
    const parsed = parseRedoQuery(`?queue=p-a,p-b&i=${encodeURIComponent(raw)}`);
    assert.equal(parsed.ok, false, `i=${raw} 必须失败`);
    assert.equal(parsed.code, 'index_invalid');
    assert.equal(parsed.details.param, 'i');
  }
});

test('parseRedoQuery：i 越界时失败并说清队列里有几道', () => {
  const parsed = parseRedoQuery('?queue=p-a,p-b&i=2');
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, 'index_out_of_range');
  assert.equal(parsed.details.count, 2);
  assert.equal(parsed.details.value, 2);
});

test('parseRedoQuery：队列里的坏 id（空元素／路径穿越）明确失败，不静默过滤', () => {
  for (const raw of ['p-a,', ',p-a', 'p-a,,p-b', '../etc/passwd']) {
    const parsed = parseRedoQuery(`?queue=${encodeURIComponent(raw)}&i=0`);
    assert.equal(parsed.ok, false, `queue=${raw} 必须失败`);
    assert.equal(parsed.code, 'queue_malformed');
  }
});

test('parseRedoQuery：basis 只认枚举，认不出就失败，不退回默认值', () => {
  assert.equal(parseRedoQuery('?queue=p-a&i=0&basis=including_cooling').basis, 'including_cooling');

  const parsed = parseRedoQuery('?queue=p-a&i=0&basis=bogus');
  assert.equal(parsed.ok, false);
  assert.equal(parsed.code, 'basis_unknown');
});

test('buildRedoUrl：URL 里原样带着队列与索引，可刷新、可开在另一个窗口', () => {
  const url = buildRedoUrl({queue: ['p-a', 'p-b'], i: 1});
  assert.equal(url, '/redo?queue=p-a%2Cp-b&i=1');
  const parsed = parseRedoQuery(url.slice(url.indexOf('?')));
  assert.deepEqual(parsed.queue, ['p-a', 'p-b']);
  assert.equal(parsed.i, 1);

  // 非默认总体才写进 URL（默认总体是服务给的 default_basis）
  const withBasis = buildRedoUrl({queue: ['p-a'], i: 0, basis: 'including_cooling'});
  assert.equal(parseRedoQuery(withBasis.slice(withBasis.indexOf('?'))).basis, 'including_cooling');
  assert.equal(
    buildRedoUrl({queue: ['p-a'], i: 0, basis: 'in_default_list'}).includes('basis='),
    false,
  );
});

test('resolveQueueItem：队列里的题查不到时明确失败，不跳过它继续', () => {
  const problems = [problem({id: 'p-a'})];
  const missing = resolveQueueItem(problems, ['p-a', 'p-gone'], 1);
  assert.equal(missing.ok, false);
  assert.equal(missing.code, 'problem_not_in_index');
  assert.equal(missing.details.id, 'p-gone');
  assert.equal(missing.details.i, 1);

  const found = resolveQueueItem(problems, ['p-a'], 0);
  assert.equal(found.ok, true);
  assert.equal(found.problem.id, 'p-a');
});

test('removeAt：判完只拿掉被judged的那一道，别的题与顺序都不许变（第 21 条）', () => {
  const queue = ['p-a', 'p-b', 'p-c'];
  const next = removeAt(queue, 1);
  assert.equal(next.ok, true);
  assert.equal(next.removed, 'p-b');
  assert.deepEqual(next.queue, ['p-a', 'p-c']);
  assert.deepEqual(queue, ['p-a', 'p-b', 'p-c'], '不许就地改原队列');

  const outOfRange = removeAt(queue, 3);
  assert.equal(outOfRange.ok, false);
  assert.equal(outOfRange.code, 'index_out_of_range');
});

// ------------------------------------- 验收 3：输入随题型分化（判据来自服务）

test('answerMode：选择题给选项', () => {
  const mode = answerMode(problem({type: 'choice'}));
  assert.equal(mode.mode, 'choice');
  assert.deepEqual(mode.options.map((o) => o.label), ['A', 'B']);
});

test('answerMode：填空题给一个空（一个输入就是一道题）', () => {
  const mode = answerMode(problem({type: 'fillin', options: []}));
  assert.equal(mode.mode, 'fillin');
});

test('answerMode：选择题但选项缺失（options: []）→ 单输入框的填空回退，不是死胡同', () => {
  // 最终修复 pass 作业单 1：索引里真有 `options: []` 的选择题（来源 #7 的只读验证者）。
  // 修前 `mode` 仍是 'choice'，而组件只在 options.length > 0 时渲染输入 → 零个 <input>、
  // 提交永远 disabled，界面上却写着一句自相矛盾的「只能自己敲答案」。
  // 标准答案还在，敲字母是合法作答（spec #1 故事 4：选择题只敲一个字母），
  // 所以正确做法是**给一个输入框**并如实说明选项缺失。
  const mode = answerMode(problem({type: 'choice', options: []}));

  assert.equal(mode.mode, 'fillin', '要回退到单输入框，而不是一个永远 disabled 的死胡同');
  assert.equal(mode.fallback, 'options_missing');
  assert.match(mode.notice, /选项/);
  assert.match(mode.notice, /敲答案/);
  assert.deepEqual(mode.options, []);
});

test('answerMode：选项齐全的选择题照旧给选项（回退不许反过来影响正常路径）', () => {
  const mode = answerMode(problem({type: 'choice'}));

  assert.equal(mode.mode, 'choice');
  assert.equal(mode.fallback, null);
  assert.deepEqual(mode.options.map((o) => o.label), ['A', 'B']);
});

test('answerMode：解答题不给作答框，理由是服务端 autojudge 的原话', () => {
  const mode = answerMode(
    problem({
      type: 'solution',
      options: [],
      auto_judge: {
        eligible: false,
        reason: 'solution_type',
        reason_text: '解答题只能人工确认：过程题在屏幕上敲不出过程',
      },
    }),
  );
  assert.equal(mode.mode, 'none');
  assert.equal(mode.reason, 'solution_type');
  assert.equal(mode.reason_text, '解答题只能人工确认：过程题在屏幕上敲不出过程');
});

test('answerMode：服务说不能自动判定就不给作答框（未审核／无标准答案同理）', () => {
  for (const reason of ['unreviewed', 'no_standard_answer']) {
    const mode = answerMode(
      problem({
        type: 'fillin',
        auto_judge: {eligible: false, reason, reason_text: `原话-${reason}`},
      }),
    );
    assert.equal(mode.mode, 'none');
    assert.equal(mode.reason_text, `原话-${reason}`);
  }
});

test('answerMode：题型枚举认不出时明确失败，不静默退回选择题', () => {
  const mode = answerMode(problem({type: 'proof', options: [{label: 'A', text: '甲'}]}));
  assert.equal(mode.mode, 'none');
  assert.equal(mode.reason, 'unknown_type');
});

test('answerMode：服务说可判但题型是解答（数据自相矛盾）时也不给作答框', () => {
  const mode = answerMode(problem({type: 'solution', options: []}));
  assert.equal(mode.mode, 'none');
  assert.equal(mode.reason, 'solution_type');
});

// ------------------------------------------- 验收 2：题面只能是擦除后的图

test('problemImage：有擦除图就用它', () => {
  const image = problemImage(problem());
  assert.equal(image.url, '/api/problem/p-a/image/clean');
  assert.equal(image.missing, false);
});

test('problemImage：缺擦除图时不回退到原图（原图印着订正）', () => {
  const image = problemImage(
    problem({images: {original: '/api/problem/p-a/image/original', clean: null, mask: null}}),
  );
  assert.equal(image.url, null);
  assert.equal(image.missing, true);
  assert.equal(JSON.stringify(image).includes('original'), false, '结果里不许出现原图');
});

// ------------------------------------------- 验收 5：页头的 N/M 照服务给的原话

test('redoHeader：按开关取服务算好的那一套，一个数字都不重算', () => {
  const data = {screen_redo: SCREEN_REDO};
  const selected = redoHeader(data, 'including_cooling');
  assert.equal(selected.known, true);
  assert.equal(selected.basis, 'including_cooling');
  assert.equal(selected.basis_text, SCREEN_REDO.bases.including_cooling.basis_text);
  assert.equal(selected.ready, 3);
  assert.equal(selected.blocked_total, 2);

  const fallback = redoHeader(data, null);
  assert.equal(fallback.basis, 'in_default_list');
  assert.equal(fallback.not_auto_judgeable.count, 2);
  assert.deepEqual(
    fallback.not_auto_judgeable.by_reason.map((r) => r.reason),
    ['solution_type', 'unreviewed'],
  );
  // M 的那句话是服务给的原话，界面照抄
  assert.equal(fallback.no_clean_image.message, '另有 1 道因缺少擦除手写后的题面图不能进屏幕重做');
});

test('redoHeader：开关指到服务没有的总体时明确失败，不悄悄用默认那套', () => {
  const selected = redoHeader({screen_redo: SCREEN_REDO}, 'bogus');
  assert.equal(selected.known, false);
  assert.equal(selected.basis, 'bogus');
  assert.deepEqual(selected.available_bases, ['in_default_list', 'including_cooling']);
});

test('queueForProblems：队列 = 能屏幕重做的题，按服务给好的顺序，不重排', () => {
  const problems = [
    problem({id: 'p-1'}),
    problem({id: 'p-sol', screen_redo: {ready: false, blockers: ['solution_type'], blocker_text: ['…']}}),
    problem({id: 'p-2', cooling: true, in_default_list: false}),
    problem({id: 'p-grad', graduated: true, in_default_list: false}),
  ];

  assert.deepEqual(queueForProblems(problems, 'in_default_list'), ['p-1']);
  assert.deepEqual(queueForProblems(problems, 'including_cooling'), ['p-1', 'p-2']);
});

// --------------------------------------- 验收 4：判定由服务做，界面只提交作答

test('screenPayload：提交给服务的只有 channel 与 answer 两个键', () => {
  const payload = screenPayload('A');
  assert.deepEqual(Object.keys(payload).sort(), ['answer', 'channel']);
  assert.equal(payload.channel, 'screen');
  assert.equal(payload.answer, 'A');
  for (const forbidden of ['verdict', 'source', 'confidence', 'provider', 'model', 'overrode']) {
    assert.equal(forbidden in payload, false, `客户端不许提交 ${forbidden}`);
  }
});

test('submitScreenAnswer：作答进去，服务给的整封回应出来', async () => {
  const calls = [];
  const post = async (pid, payload) => {
    calls.push([pid, payload]);
    return {ok: true, data: {attempt: {verdict: 'correct'}, mastery: {credited: true, streak: 1}}};
  };
  const outcome = await submitScreenAnswer({pid: 'p-a', answer: 'A', post});
  assert.equal(outcome.ok, true);
  assert.deepEqual(calls, [['p-a', {channel: 'screen', answer: 'A'}]]);
  assert.equal(outcome.envelope.data.mastery.credited, true);
});

test('submitScreenAnswer：服务拒绝时照原话带回来，界面不自己映射对错', async () => {
  const error = new Error('解答题只能人工确认：过程题在屏幕上敲不出过程');
  Object.assign(error, {code: 'not_auto_judgeable', reason: 'solution_type', status: 422, hint: '只能在纸上重做'});
  const outcome = await submitScreenAnswer({pid: 'p-sol', answer: 'x', post: async () => { throw error; }});

  assert.equal(outcome.ok, false);
  assert.equal(outcome.code, 'not_auto_judgeable');
  assert.equal(outcome.reason, 'solution_type');
  assert.equal(outcome.message, '解答题只能人工确认：过程题在屏幕上敲不出过程');
  assert.equal(outcome.hint, '只能在纸上重做');
  assert.equal(outcome.status, 422);
  assert.equal('verdict' in outcome, false, '界面不许自己给一个判定');
});

test('attemptResult / creditLine：判定与掌握读数一律照服务给的原话', () => {
  const data = {
    attempt: {
      at: '2026-10-04T09:39:57+00:00',
      channel: 'screen',
      verdict: 'correct',
      source: 'auto',
      confidence: 0.95,
      provider: 'deepseek',
      model: 'deepseek-flash',
      error_causes: [],
      note: '判对且脱离冷却：连续正确 1/2',
    },
    mastery: {state: 'in_pool', streak: 1, credited: true, cooling: false, note: '判对且脱离冷却：连续正确 1/2'},
    run_id: '20261004-090000-000-judge.json',
  };
  const result = attemptResult(data);
  assert.equal(result.verdict, 'correct');       // 原样，不许界面翻译成「对」
  assert.equal(result.confidence, 0.95);
  assert.equal(result.provider, 'deepseek');
  assert.equal(result.model, 'deepseek-flash');
  assert.equal(result.note, '判对且脱离冷却：连续正确 1/2');
  assert.equal(result.run_id, '20261004-090000-000-judge.json');

  assert.equal(creditLine(result).text, '已计入 1/2');
  assert.equal(creditLine({credited: false, streak: 1}).text, '只热身，不计入掌握');
});
