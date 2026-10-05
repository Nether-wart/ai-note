/**
 * 打包产物安全的那几条（`node --test site/tests/`）。
 *
 * 起因是一次**node 全绿、浏览器全崩**：源码里写的是 `[...map.values()]`，打包器在
 * loose 模式下把展开编译成 `[].concat(map.values())`——而 `concat` **不展开迭代器**，
 * 它把 MapIterator 本身当成一个元素装进数组。于是 `.map((bucket) => bucket.problems…)`
 * 在浏览器里读到 `undefined.problems`，整页崩掉；而 `node --test` 走的是源码语义，
 * 一路是绿的。
 *
 * 这类坑**没有别的便宜办法提前发现**：语义在源码里是对的，坏在变换那一步。
 * 所以这里做一件很窄的事——**在源码层禁掉「展开迭代器」这种写法**，
 * 逼着它写成 `Array.from(...)`（普通调用，任何模式下语义一致）。
 *
 * 说清它**不**管什么：别的 loose 变换（例如 `for...of` 的下标化、类字段、
 * 参数默认值）这里一条都不查。它只钉住已经真咬过一次的那一条。
 */

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {join} from 'node:path';

const SRC = new URL('../src/', import.meta.url).pathname;

/** 迭代器／集合上的展开：`[...x.values()]`、`[...x.keys()]`、`[...x.entries()]`、
 *  `[...someSet]`。形如 `[...arr]` 的普通数组展开是安全的，不在这一列。 */
const RISKY = [
  /\[\s*\.\.\.[A-Za-z_$][\w$.]*\.(values|keys|entries)\(\)\s*\]/g,
  /\[\s*\.\.\.[A-Za-z_$][\w$]*(Set|Map)\s*\([^)]*\)\s*\]/g,
];

/**
 * 先把注释剥掉再查。
 *
 * 这条不是洁癖：解释这条规矩的注释里**必然**会出现那个坏写法（「不许写成 `[...x.values()]`」），
 * 不剥的话这条检查会被自己的说明文档判红——和当初那行 `# noqa: N802（中文说明）`
 * 被 ruff 当成非法指令是同一个自指的坑。
 */
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

test('源码里不许展开迭代器（打包器会把它编译成 concat，语义在浏览器里变掉）', () => {
  const offenders = [];
  for (const path of walk(SRC)) {
    if (!path.endsWith('.js')) continue;
    const text = stripComments(readFileSync(path, 'utf8'));
    for (const pattern of RISKY) {
      for (const hit of text.matchAll(pattern)) {
        offenders.push(`${path.replace(SRC, 'src/')}: ${hit[0].trim()}`);
      }
    }
  }
  assert.deepEqual(offenders, [],
                   `这几处要改成 \`Array.from(...)\`：\n  ${offenders.join('\n  ')}`);
});

test('这条检查对真写法会响（它自己不是一条永远绿的判据）', () => {
  // 夹具就是当初真的崩过的那一行——判据对它会响，对 Array.from 不会。
  const bad = 'const months = [...byMonth.values()].sort(f);';
  const good = 'const months = Array.from(byMonth.values()).sort(f);';
  assert.ok(RISKY.some((re) => re.test(bad)), '对坏写法要响');
  assert.ok(!RISKY.some((re) => re.test(good)), '对 Array.from 要放行');
});
