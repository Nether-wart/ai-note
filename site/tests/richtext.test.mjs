/**
 * 带公式的正文怎么切（`site/src/lib/richtext.js`）。
 *
 * 渲染那一半（KaTeX／Mermaid）在组件里，测不了也不该在这里测；
 * 这里钉的是**切分规则**——它错的时候症状很难看：一整段正文被当成公式吞掉。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';

import {mathKind, mathParts, segments} from '../src/lib/richtext.js';

test('公式与普通文字交替切开，分隔符留在片段里', () => {
  assert.deepEqual(mathParts('a $x^2$ b'), ['a ', '$x^2$', ' b']);
  assert.deepEqual(mathParts('$$y$$'), ['$$y$$']);
  assert.deepEqual(mathParts('没有公式'), ['没有公式']);
  assert.deepEqual(mathParts(''), []);
});

test('行内公式不跨行——转录里落单的 $ 不许把中间整段吞成公式', () => {
  const parts = mathParts('价格为 $5\n这里是正文 $x$');
  assert.deepEqual(parts, ['价格为 $5\n这里是正文 ', '$x$']);
});

test('mermaid 围栏与别名都认；围栏外的文本仍是文本', () => {
  const out = segments('前\n```mermaid\ngraph TD; A-->B\n```\n后');
  assert.deepEqual(out.map((s) => s.kind), ['text', 'mermaid', 'text']);
  assert.equal(out[1].value, 'graph TD; A-->B\n');
  assert.deepEqual(segments('```mmd\nA\n```').map((s) => s.kind), ['mermaid']);
  // 没有围栏时只剩一段文本（不是空数组——空数组会让组件渲染出空白）
  assert.deepEqual(segments('就是一段话'), [{kind: 'text', value: '就是一段话'}]);
});

test('mathKind 认行内与独立成行，别的一律 null', () => {
  assert.deepEqual(mathKind('$a$'), {display: false, tex: 'a'});
  assert.deepEqual(mathKind('$$a$$'), {display: true, tex: 'a'});
  assert.equal(mathKind('$'), null);
  assert.equal(mathKind('$$'), null);
  assert.equal(mathKind('普通文字'), null);
  // 非字符串不该把组件炸掉
  assert.deepEqual(segments(null), [{kind: 'text', value: ''}]);
  assert.deepEqual(mathParts(undefined), []);
});
