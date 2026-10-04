/**
 * 给 pytest 用的探针（#8）：让 `server/tests/test_ui_invariants.py` 断言**同一份**
 * 判据，而不是把界面不变量的规则在 Python 里再抄一遍（照 #7 `probe-redo.mjs` 的做法）。
 *
 * stdin：`[{"fn": "checkQuestionImage", "args": [{…}]}, …]`
 * stdout：`[{…违规数组…}, …]`
 *
 * 只跑纯函数（无 DOM、无网络、无依赖）——没装 node_modules 也能用。
 */
import {readFileSync} from 'node:fs';

import * as invariants from './invariants.mjs';

const requests = JSON.parse(readFileSync(0, 'utf8'));
const results = requests.map(({fn, args}) => {
  const target = invariants[fn];
  if (typeof target !== 'function') {
    throw new Error(`invariants.mjs 里没有导出这个函数：${fn}`);
  }
  return target(...args);
});
process.stdout.write(JSON.stringify(results));
