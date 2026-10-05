/**
 * 侧栏那棵树与地址的形状（#17）。
 *
 * 这一批盯的是三条**会静默出事**的规则：
 *   1. 一道题都不许少——表外科目的题也必须出现在树上；
 *   2. 细则按**时刻**由新到老，不是按字符串（`+08:00` 与 `+00:00` 混在一起时字符串比是错的）；
 *   3. 地址缺参数、栏位认不出时**明确失败**，绝不落到一个默认值。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {
  detailOrder,
  outlineRows,
  overview,
  problemsOfSubject,
  sidebarTree,
  subjectLabel,
} from '../src/lib/tree.js';
import {
  DEFAULT_VIEW,
  parseProblemQuery,
  parseSubjectQuery,
  problemUrl,
  subjectUrl,
  viewLabels,
} from '../src/lib/routes.js';

const INDEX = {
  subjects: ['数学', '物理'],
  outline: {
    数学: {函数与导数: {极值与最值: [], 导数与单调性: ['切线斜率']}},
    物理: {},
  },
  stats: {
    problems: 4,
    in_default_list: 1,
    cooling: 2,
    graduated: 1,
    auto_judge_eligible: 3,
    unclassified: 1,
    by_subject: {
      数学: {problems: 2, in_default_list: 1, cooling: 1, graduated: 0,
             auto_judge_eligible: 2, unreviewed: 1},
      物理: {problems: 1, in_default_list: 0, cooling: 1, graduated: 1,
             auto_judge_eligible: 1, unreviewed: 0},
      // 表外的科目：卡上写了一个不在词表里的值。它**必须**还在树上。
      化学: {problems: 1, in_default_list: 0, cooling: 0, graduated: 0,
             auto_judge_eligible: 0, unreviewed: 1},
    },
  },
  briefs: {
    数学: {latest_date: '2026-10-05', generated_at: '2026-10-05T01:00:00+00:00',
           stale: true, new_problems: 2},
  },
  warnings: [],
};

const PROBLEMS = [
  {id: 'p-1', subject: '数学', created_at: '2026-10-04T14:31:35+08:00'},
  {id: 'p-2', subject: '数学', created_at: '2026-10-04T06:31:35+00:00'},
  {id: 'p-3', subject: '化学', created_at: '2026-10-05T00:00:00+08:00'},
  {id: 'p-4', subject: null, created_at: '2026-10-03T00:00:00+08:00'},
  {id: 'p-5', subject: '数学', created_at: null},
];

// ------------------------------------------------------------- 侧栏那棵树

test('词表里的科目哪怕 0 道也在树上', () => {
  const tree = sidebarTree(INDEX);
  const names = tree.subjects.map((s) => s.name);
  assert.deepEqual(names.slice(0, 2), ['数学', '物理']);
  assert.equal(tree.subjects[1].counts.problems, 1);
});

test('表外科目的题也在树上（一道题都不许少）', () => {
  const tree = sidebarTree(INDEX);
  const chem = tree.subjects.find((s) => s.name === '化学');
  assert.ok(chem, '不在词表里的科目被从树上丢掉了——那样它的题就没有入口');
  assert.equal(chem.offVocabulary, true);
  assert.equal(chem.counts.problems, 1);
  assert.equal(tree.subjects.find((s) => s.name === '数学').offVocabulary, false);
});

test('服务没给某个科目的桶时不许装作 0 道', () => {
  const tree = sidebarTree({subjects: ['数学', '地理'], stats: {by_subject: {数学: {problems: 3}}}});
  const geo = tree.subjects.find((s) => s.name === '地理');
  assert.equal(geo.countsMissing, true);
  assert.equal(geo.counts.problems, 0);
});

test('未归类是一等状态，不是空名字', () => {
  const tree = sidebarTree(INDEX);
  assert.equal(tree.unclassified.count, 1);
  assert.equal(tree.unclassified.label, '未归类');
  assert.equal(subjectLabel(null), '未归类');
  assert.equal(subjectLabel('数学'), '数学');
});

test('简报读数照抄服务给的那一份，不重算', () => {
  const tree = sidebarTree(INDEX);
  assert.deepEqual(tree.subjects.find((s) => s.name === '数学').brief,
                   INDEX.briefs.数学);
  assert.equal(tree.subjects.find((s) => s.name === '物理').brief, null);
});

// ------------------------------------------------------------------ 取题

test('未归类那一栏取的是没有科目的题', () => {
  assert.deepEqual(problemsOfSubject(PROBLEMS, null).map((p) => p.id), ['p-4']);
  assert.deepEqual(problemsOfSubject(PROBLEMS, '').map((p) => p.id), ['p-4']);
  assert.deepEqual(problemsOfSubject(PROBLEMS, '数学').map((p) => p.id), ['p-1', 'p-2', 'p-5']);
});

// ------------------------------------------------------------------ 排序

test('细则按时刻由新到老——不是按字符串', () => {
  const {ordered} = detailOrder([
    {id: 'a', created_at: '2026-10-04T06:31:35+00:00'},   // 14:31 +08:00 更早
    {id: 'b', created_at: '2026-10-04T14:31:35+08:00'},   // 06:31 UTC
  ]);
  // 按字符串比会得到 b 在前（"2026-10-04T14" > "2026-10-04T06"），按时刻比是 a 在前
  assert.deepEqual(ordered.map((p) => p.id), ['a', 'b']);
});

test('没有录入时间的题排在最后，而且**逐个报出来**', () => {
  const {ordered, undated} = detailOrder(PROBLEMS);
  assert.deepEqual(ordered.map((p) => p.id), ['p-3', 'p-1', 'p-2', 'p-4', 'p-5']);
  assert.deepEqual(undated.map((p) => p.id), ['p-5']);
});

// ------------------------------------------------------------------ 大纲

test('大纲按 章 → 节 → 点 摊开，空点集原样保留', () => {
  const rows = outlineRows(INDEX.outline, '数学');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].chapter, '函数与导数');
  assert.deepEqual(rows[0].sections.map((s) => s.section), ['极值与最值', '导数与单调性']);
  assert.deepEqual(rows[0].sections[0].points, []);
  assert.deepEqual(rows[0].sections[1].points, ['切线斜率']);
  assert.deepEqual(outlineRows(INDEX.outline, '物理'), []);
  assert.deepEqual(outlineRows(INDEX.outline, '化学'), []);
});

// ------------------------------------------------------------------ 首页

test('首页总览把服务给的三样读数摆在一起，不另算一个指标', () => {
  const home = overview(INDEX);
  const math = home.rows.find((r) => r.name === '数学');
  assert.deepEqual(math.today, {ready: 1, cooling: 1, unreviewed: 1, graduated: 0});
  assert.equal(home.unclassified.count, 1);
  assert.equal(home.totals.problems, 4);
});

test('词表缺席时总览把它挑出来交给页面显示', () => {
  const home = overview({
    subjects: [],
    stats: {problems: 1, by_subject: {}},
    warnings: [{code: 'subjects_vocab_missing', level: 'warning', message: '没有科目词表'}],
  });
  assert.equal(home.vocabularyWarning.code, 'subjects_vocab_missing');
  assert.equal(overview(INDEX).vocabularyWarning, null);
});

// ------------------------------------------------------------------ 地址

test('地址的形状：未归类那一栏不带 name', () => {
  assert.equal(subjectUrl('数学', 'detail'), '/subject?name=%E6%95%B0%E5%AD%A6&view=detail');
  assert.equal(subjectUrl(null, 'brief'), '/subject?view=brief');
  assert.equal(problemUrl('p-1'), '/problem?pid=p-1');
});

test('时间筛选也跟着地址走（筛完的视图要能刷新、能分享）', () => {
  assert.equal(subjectUrl('数学', 'detail', {month: '2026-10', week: '2026-09-28'}),
               '/subject?name=%E6%95%B0%E5%AD%A6&view=detail&month=2026-10&week=2026-09-28');
  assert.equal(subjectUrl(null, 'detail', {month: '2026-10'}),
               '/subject?view=detail&month=2026-10');
});

test('栏位缺省是简报，认不出的栏位**明确失败**', () => {
  assert.deepEqual(parseSubjectQuery('?name=数学'),
                   {name: '数学', view: DEFAULT_VIEW, month: null, week: null});
  assert.deepEqual(parseSubjectQuery(''),
                   {name: null, view: DEFAULT_VIEW, month: null, week: null});
  assert.deepEqual(parseSubjectQuery('?view=detail&month=2026-10&week=2026-09-28'),
                   {name: null, view: 'detail', month: '2026-10', week: '2026-09-28'});
  const bad = parseSubjectQuery('?view=whatever');
  assert.equal(bad.error.code, 'view_unknown');
  assert.ok(bad.error.message.includes('whatever'));
  assert.ok(viewLabels().includes(DEFAULT_VIEW));
});

test('时间筛选的形状在这里验、存不存在留给数据那一层', () => {
  assert.equal(parseSubjectQuery('?month=2026-1').error.code, 'month_malformed');
  assert.equal(parseSubjectQuery('?month=2026-10&week=10-05').error.code, 'week_malformed');
  // 周是**某个月里**的周：单独给周是一次不完整的筛选，不该被猜成「这个月」
  assert.equal(parseSubjectQuery('?week=2026-10-05').error.code, 'week_without_month');
  // 只有「细则」按时间索引；别的栏位收到这两个参数要**明确失败**，不许静默忽略
  assert.equal(parseSubjectQuery('?view=brief&month=2026-10').error.code,
               'time_filter_needs_detail');
  // 形状对但索引里没有那一桶 → 这一层放行（`lib/time.js` 的 `filterByTime` 会明确失败）
  assert.equal(parseSubjectQuery('?month=2026-07&view=detail').month, '2026-07');
});

test('「读不出时刻」只有一条判据：排序与时间索引对同一道题说同一句话', async () => {
  const {timeIndex} = await import('../src/lib/time.js');
  // V8 的 `Date.parse` 认这种写法，而它**不是**我们认的形状：
  // 若两处各判各的，这道题会排在表里、却进不了任何一张月卡、也不算「没有录入时间」。
  const odd = {id: 'p-odd', created_at: '10/04/2026'};
  const normal = {id: 'p-ok', created_at: '2026-10-04T14:31:35+08:00'};

  assert.deepEqual(detailOrder([odd, normal]).undated.map((p) => p.id), ['p-odd']);
  assert.deepEqual(timeIndex([odd, normal]).undated.map((p) => p.id), ['p-odd']);
});

test('阅读页没有 pid 时明确失败——绝不落到某一道题', () => {
  assert.deepEqual(parseProblemQuery('?pid=p-9'), {pid: 'p-9'});
  const missing = parseProblemQuery('?pid=');
  assert.equal(missing.error.code, 'pid_missing');
  assert.ok(missing.error.hint.includes('/problem?pid='));
});
