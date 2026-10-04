/**
 * 给 pytest 用的探针：让 `server/tests/test_site_redo_queue.py` 能断言**同一份**
 * `src/lib/redo.js`，而不是把队列规则在 Python 里再抄一遍。
 *
 * stdin：`[{"fn": "parseRedoQuery", "args": ["?queue=p-a&i=0"]}, …]`
 * stdout：`[{…返回值…}, …]`
 *
 * 只跑纯函数（无 DOM、无网络、无依赖）——没装 node_modules 也能用。
 */
import {readFileSync} from 'node:fs';

import * as redo from '../src/lib/redo.js';

const requests = JSON.parse(readFileSync(0, 'utf8'));
const results = requests.map(({fn, args}) => {
  const target = redo[fn];
  if (typeof target !== 'function') {
    throw new Error(`redo.js 里没有导出这个函数：${fn}`);
  }
  return target(...args);
});
process.stdout.write(JSON.stringify(results));
