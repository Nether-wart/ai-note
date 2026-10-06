/**
 * 审核页的纯逻辑（`site/src/lib/review.js`）。
 *
 * 最要紧的一条：**没动过、草稿里也没有的空字段，一个字都不许发**。
 * 服务端把 `""` 当成"显式清空"，误发空值就是把内容清掉——这类错**不会有任何报错**。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {
  buildTranscriptPayload, describeReadout, describeSaved, formFromReadout,
  hasSomethingToSave, pendingFromIndex,
} from '../src/lib/review.js';

const INDEX = {
  // 外壳的真实状态形状：警告在 `envelope.warnings` 里（不是 `warnings`）
  phase: 'ready',
  envelope: {
    warnings: [
      {code: 'problem_transcript_missing', id: 'p-b', level: 'warning'},
      {code: 'topics_empty', id: 'p-b', level: 'warning'},
      {code: 'problem_transcript_missing', id: 'p-c', level: 'warning'},
      {code: 'problem_transcript_missing', id: 'p-c', level: 'warning'},
      {code: 'standard_answer_missing', id: 'p-a', level: 'hint'},
    ],
  },
  data: {problems: [
    {id: 'p-a', subject: '数学', created_at: '2026-10-04T14:31:35+08:00'},
    {id: 'p-b', subject: '物理', created_at: '2026-09-01T09:00:00+08:00'},
    {id: 'p-c', subject: '化学', created_at: '2026-10-05T01:00:00+08:00'},
  ]},
};

test('待审清单从索引警告里筛，去重，由新到老', () => {
  const rows = pendingFromIndex(INDEX);
  assert.deepEqual(rows.map((row) => row.id), ['p-c', 'p-b'], '证据足够新到老，且不重复');
  assert.equal(rows[0].subject, '化学', '要带上卡上的科目，界面才画得出来');
});

test('没有 warning 或没有索引时不炸', () => {
  assert.deepEqual(pendingFromIndex(null), []);
  assert.deepEqual(pendingFromIndex({data: {problems: []}}), []);
  assert.deepEqual(pendingFromIndex({envelope: {warnings: []}}), []);
});

const READOUT = {
  ok: true, ocr_engine: 'unavailable', ocr_draft_chars: 0,
  result: {transcript: '6. 题面', options: [], topics: ['函数与导数/极值与最值'],
           error_causes: [], standard_answer: {value: 'B', confidence: 0.9},
           unreadable: [], notes: null, parsed: true, provider: 'openai', model: 'gpt-4o'},
};

test('读数 → 表单：草稿里的东西都在，且都还没被"动过"', () => {
  const form = formFromReadout(READOUT);
  assert.equal(form.transcript, '6. 题面');
  assert.deepEqual(form.topics, ['函数与导数/极值与最值']);
  assert.deepEqual(form.standard_answer, {value: 'B', confidence: 0.9});
  assert.equal(form.touched.transcript, false);
});

test('没动过也不许发空值——服务端把空串当"显式清空"', () => {
  const form = formFromReadout({ok: true, result: {transcript: '', options: []}});
  assert.deepEqual(buildTranscriptPayload(form), {},
                   '空草稿 + 没动过 = 一个字段都不发（发了就是清空）');
  assert.equal(hasSomethingToSave(form), false, '这时"保存"该是禁用的');
});

test('没动过但草稿里非空 → 发出去（那就是"确认草稿"）', () => {
  const payload = buildTranscriptPayload(formFromReadout(READOUT));
  assert.equal(payload.transcript, '6. 题面');
  assert.deepEqual(payload.topics, ['函数与导数/极值与最值']);
  assert.deepEqual(payload.standard_answer, {value: 'B', confidence: 0.9});
  assert.equal('mark_reviewed' in payload, false, '不勾就不动审核状态');
});

test('人动过的（哪怕动成空）→ 发出去，包括空串', () => {
  const form = formFromReadout(READOUT);
  form.transcript = '';
  form.touched.transcript = true;
  assert.equal(buildTranscriptPayload(form).transcript, '', '人明确说"这里没有"要能表达');

  const cleared = formFromReadout(READOUT);
  cleared.standard_answer = null;
  cleared.touched.standard_answer = true;
  assert.equal(buildTranscriptPayload(cleared).standard_answer, null);
});

test('勾了"确认并标记已审核"才带 mark_reviewed', () => {
  const form = formFromReadout(READOUT);
  assert.equal(buildTranscriptPayload(form, {markReviewed: true}).mark_reviewed, true);
  assert.equal(hasSomethingToSave(formFromReadout({ok: true, result: {}}), {markReviewed: true}),
               true, '只标记已审核也该能提交');
});

test('读数的说明用服务的原话（含 OCR 到底可不可用）', () => {
  const ok = describeReadout(READOUT);
  assert.equal(ok.ok, true);
  assert.ok(ok.lines.join(' ').includes('openai / gpt-4o'));
  assert.ok(ok.lines.join(' ').includes('本地 OCR：不可用'));

  const failed = describeReadout({ok: false, error: {message: '上游 500', hint: '可以重试'}});
  assert.equal(failed.ok, false);
  assert.ok(failed.lines.join(' ').includes('上游 500'));
  assert.ok(failed.lines.join(' ').includes('可以重试'));
});

test('保存结果：成功说改了几项，失败说服务那句话', () => {
  assert.ok(describeSaved(200, {data: {changed: ['problem.transcript']}}).text.includes('1 项'));
  assert.ok(describeSaved(200, {data: {changed: []}}).text.includes('没有改动'));
  const bad = describeSaved(400, {error: {message: '错因不在受控词表里', hint: '可选：概念不清'}});
  assert.equal(bad.ok, false);
  assert.ok(bad.text.includes('概念不清'));
});
