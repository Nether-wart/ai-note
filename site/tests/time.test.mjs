/**
 * 时间那一层（`site/src/lib/time.js`）：显示成人读的样子 + 按月／周索引。
 *
 * 盯住三件事：
 *   1. 显示**按原样**渲染（不做时区换算），读不出时给一句明说而不是空位；
 *   2. 排不了时间的题进 `undated`，**不丢**；
 *   3. 认不出的月份／周**明确失败**，不悄悄退成「全部」。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {
  filterByTime,
  formatDay,
  formatMoment,
  isoWeekNo,
  mondayOf,
  monthLabel,
  parseMoment,
  timeIndex,
} from '../src/lib/time.js';

const PROBLEMS = [
  {id: 'p-a', created_at: '2026-10-07T14:31:35+08:00'},   // 周三 → 那一周周一 10-05
  {id: 'p-b', created_at: '2026-10-04T09:02:00+08:00'},   // 周日 → 那一周周一 09-28
  {id: 'p-c', created_at: '2026-10-01T08:00:00+08:00'},   // 周四 → 与 p-b **同一周**（09-28）
  {id: 'p-d', created_at: '2026-09-20T10:00:00+08:00'},
  {id: 'p-e', created_at: '2026-08-31T10:00:00+08:00'},   // 08-31 是周一
  {id: 'p-f', created_at: null},
  {id: 'p-g', created_at: '看不太清'},
];

// ------------------------------------------------------------------ 显示

test('时刻显示成人读的样子，原样不换算', () => {
  assert.deepEqual(formatMoment('2026-10-04T14:31:35+08:00'),
                   {text: '2026-10-04 14:31', exact: '2026-10-04T14:31:35+08:00'});
  // 带 +00:00 的也**照它自己写的**渲染（界面上出现的只有题卡的 created_at，是 +08:00；
  // 真做换算会与「重做历史只有日期」那套口径打架）
  assert.equal(formatMoment('2026-10-04T09:39:57+00:00').text, '2026-10-04 09:39');
  // 只有日期也认
  assert.equal(formatMoment('2026-10-04').text, '2026-10-04');
  assert.equal(formatDay('2026-10-04T14:31:35+08:00'), '2026-10-04');
});

test('读不出的时刻明说读不出，不给空位', () => {
  for (const bad of [null, undefined, '', '看不太清', 20261004]) {
    const out = formatMoment(bad);
    assert.equal(out.text, '（服务没给时刻）', String(bad));
    assert.equal(out.exact, null);
  }
});

test('月份标题是中文', () => {
  assert.equal(monthLabel('2026-10'), '2026 年 10 月');
  assert.equal(monthLabel('2026-01'), '2026 年 1 月');
});

// ------------------------------------------------------------------ 周

test('周一是本周第一天，且用 UTC 运算不被本机时区挪走', () => {
  assert.equal(mondayOf(parseMoment('2026-10-04')).day_key, '2026-09-28');  // 周日 → 上一周一
  assert.equal(mondayOf(parseMoment('2026-10-05')).day_key, '2026-10-05');  // 周一 → 自己
  assert.equal(mondayOf(parseMoment('2026-10-11')).day_key, '2026-10-05');  // 周日 → 那一周一
});

test('ISO 周号', () => {
  assert.equal(isoWeekNo(parseMoment('2026-10-05')), 41);
  assert.equal(isoWeekNo(parseMoment('2026-01-01')), 1);
  assert.equal(isoWeekNo(parseMoment('2026-12-31')), 53);
});

// ------------------------------------------------------------------ 索引

test('按月索引、由新到老，最近那一个月再按周', () => {
  const index = timeIndex(PROBLEMS);

  assert.deepEqual(index.months.map((m) => m.key), ['2026-10', '2026-09', '2026-08']);
  assert.deepEqual(index.months.map((m) => m.count), [3, 1, 1]);
  assert.equal(index.months[0].label, '2026 年 10 月');

  // 只有**最近**那一个月有周；周也由新到老
  assert.equal(index.months[1].weeks, undefined);
  const weeks = index.months[0].weeks;
  assert.deepEqual(weeks.map((w) => w.key), ['2026-10-05', '2026-09-28']);
  assert.deepEqual(weeks.map((w) => w.count), [1, 2]);
  // 10-01（周四）与 10-04（周日）**同一周**（周一是 09-28）——标签给那一周真实的起止
  assert.deepEqual(weeks[1].ids, ['p-b', 'p-c']);
  assert.equal(weeks[1].label, '9月28日–10月4日');
  assert.equal(weeks[0].label, '10月5日–10月11日');
  assert.deepEqual(weeks.map((w) => w.week_no), [41, 40]);
});

test('排不了时间的题进 undated，一道都不丢', () => {
  const index = timeIndex(PROBLEMS);
  assert.deepEqual(index.undated.map((p) => p.id), ['p-f', 'p-g']);

  const total = index.months.reduce((sum, m) => sum + m.count, 0) + index.undated.length;
  assert.equal(total, PROBLEMS.length, '按月数出来的道数必须等于总数');
});

// ------------------------------------------------------------------ 筛选

test('没给筛选就是全部', () => {
  const out = filterByTime(PROBLEMS, {});
  assert.equal(out.problems.length, PROBLEMS.length);
  assert.equal(out.label, null);
});

test('按周筛选取的是那一周里的题', () => {
  const out = filterByTime(PROBLEMS, {month: '2026-10', week: '2026-09-28'});
  assert.deepEqual(out.problems.map((p) => p.id), ['p-b', 'p-c']);
  assert.equal(out.label, '9月28日–10月4日');
});

test('按月筛选取的是那一个月里的题', () => {
  const out = filterByTime(PROBLEMS, {month: '2026-10'});
  assert.deepEqual(out.problems.map((p) => p.id), ['p-a', 'p-b', 'p-c']);
  assert.equal(out.label, '2026 年 10 月');
});

test('认不出的月份／周明确失败，不悄悄退成全部', () => {
  const badMonth = filterByTime(PROBLEMS, {month: '2026-07'});
  assert.equal(badMonth.error.code, 'month_unknown');
  assert.equal(badMonth.problems, undefined);

  const badWeek = filterByTime(PROBLEMS, {month: '2026-10', week: '2026-10-12'});
  assert.equal(badWeek.error.code, 'week_unknown');
  assert.ok(badWeek.error.hint.includes('YYYY-MM-DD'));
});

test('周必须真的属于那个月——不许去别的月份里捞', () => {
  // 2026-10-05 那一周属于 10 月；问 9 月要它，是一次自相矛盾的链接，不是一次正常跳转
  const crossed = filterByTime(PROBLEMS, {month: '2026-09', week: '2026-10-05'});
  assert.equal(crossed.error.code, 'week_unknown');
  assert.ok(crossed.error.hint.includes('最近那一个月'), crossed.error.hint);

  // 同一个周键，月份给对了就正常
  assert.deepEqual(filterByTime(PROBLEMS, {month: '2026-10', week: '2026-10-05'}).problems
                     .map((p) => p.id), ['p-a']);
});

test('时刻的范围要验：形状对但时刻不存在的串算读不出', () => {
  assert.equal(parseMoment('2026-10-04T25:99'), null);
  assert.equal(parseMoment('2026-13-01'), null);
  assert.equal(parseMoment('2026-00-10'), null);
  assert.equal(parseMoment('2026-10-04T23:59').hour, 23);
});
