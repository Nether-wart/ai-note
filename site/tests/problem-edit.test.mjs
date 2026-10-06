/**
 * 题卡属性编辑表单的纯逻辑（`site/src/lib/problem-edit.js`）。
 *
 * 最要紧的一条：**只提交改动过的字段**。整份回显再整份提交，会让回执里的 `changed`
 * 变成"用户看着改了一堆、其实什么都没改"——那个设置页踩过同一条。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {
  addItem, buildPatch, describeSave, formFromCard, removeItem, reviewStatus, validateEdit,
} from '../src/lib/problem-edit.js';

const CARD = {
  id: 'p-1', subject: '数学', topics: ['函数与导数/极值与最值'], error_causes: ['概念不清'],
  review: {status: 'unreviewed', reviewed_at: null, review_reopened_because: '答案抄错了'},
};

test('卡 → 表单：四个可改字段，缺的按"还没有"处理', () => {
  assert.deepEqual(formFromCard(CARD), {
    subject: '数学', topics: ['函数与导数/极值与最值'], error_causes: ['概念不清'],
    review: 'unreviewed',
  });
  assert.deepEqual(formFromCard({}), {subject: null, topics: [], error_causes: [], review: 'unreviewed'});
  assert.deepEqual(formFromCard(undefined).topics, [], '没有卡也不许炸');
});

test('review 是对象或字符串都认（卡上与详情读数的形状不同）', () => {
  assert.equal(reviewStatus({status: 'reviewed'}), 'reviewed');
  assert.equal(reviewStatus('reviewed'), 'reviewed');
  assert.equal(reviewStatus('whatever'), 'unreviewed');
  assert.equal(reviewStatus(undefined), 'unreviewed');
});

test('改了什么就只提交什么', () => {
  const form = formFromCard(CARD);
  form.subject = '物理';
  assert.deepEqual(buildPatch(form, CARD), {subject: '物理'});

  form.error_causes = ['概念不清', '计算失误'];
  assert.deepEqual(buildPatch(form, CARD), {subject: '物理', error_causes: ['概念不清', '计算失误']});
});

test('什么都没改就是空对象（不是"整份回显"）', () => {
  assert.deepEqual(buildPatch(formFromCard(CARD), CARD), {});
});

test('未归类用 null 提交（空串不是未归类）', () => {
  const form = formFromCard(CARD);
  form.subject = null;
  assert.deepEqual(buildPatch(form, CARD), {subject: null});
});

test('考点的顺序算改动、顺序不同就是改动', () => {
  const form = formFromCard(CARD);
  form.topics = ['函数与导数/极值与最值'];
  assert.deepEqual(buildPatch(form, CARD), {}, '同一个列表不算改');

  form.topics = ['函数与导数/极值与最值', '数列/求和'];
  assert.deepEqual(buildPatch(form, CARD).topics, ['函数与导数/极值与最值', '数列/求和']);
});

test('加一项：去空白、去重、保序；加空的不算加', () => {
  assert.deepEqual(addItem([], ' 数列/求和 '), ['数列/求和']);
  assert.deepEqual(addItem(['a'], 'a'), ['a']);
  assert.deepEqual(addItem(['a'], '   '), ['a']);
  assert.deepEqual(addItem(['a', 'b'], 'c'), ['a', 'b', 'c']);
  assert.deepEqual(removeItem(['a', 'b'], 'a'), ['b']);
});

test('自检：科目不在词表里、列表里有空项（服务端仍会再验一遍）', () => {
  assert.deepEqual(validateEdit(formFromCard(CARD), {subjects: ['数学', '物理']}), []);

  const bad = formFromCard(CARD);
  bad.subject = '体育';
  assert.ok(validateEdit(bad, {subjects: ['数学', '物理']})[0].includes('不在受控词表'));

  const blank = formFromCard(CARD);
  blank.topics = ['  '];
  assert.ok(validateEdit(blank).some((p) => p.includes('考点')));

  // 拿不到词表时不猜（服务端才是权威）
  const unknown = formFromCard(CARD);
  unknown.subject = '体育';
  assert.deepEqual(validateEdit(unknown, {subjects: null}), []);
});

test('保存结果用服务说的话，连可选值一起显示', () => {
  const ok = describeSave(200, {data: {card: {}, changed: ['subject', 'review']}});
  assert.equal(ok.ok, true);
  assert.ok(ok.text.includes('2 项'));

  const nothing = describeSave(200, {data: {card: {}, changed: []}});
  assert.ok(nothing.text.includes('没有改动'), nothing.text);

  const bad = describeSave(400, {error: {message: '错因不在受控词表里：心情不好',
                                       details: {param: 'error_causes', allowed: ['概念不清']}}});
  assert.equal(bad.ok, false);
  assert.ok(bad.text.includes('心情不好'));
  assert.ok(bad.text.includes('概念不清'), '服务把可选值带回来了，界面照原样显示');
});
