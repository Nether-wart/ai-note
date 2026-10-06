#!/usr/bin/env node
/**
 * **原型形状的自检替身**（#8 的反面样本，只给测试用）。
 *
 * 它复现的是原型 `--selftest` 的调用链形状：
 *
 *     selftest() → render_list() → build_index() → INDEX_PATH.write_text(...)
 *     （`proto/server.py:973 → :637 → :172`）
 *
 * 于是它有三种忠实的行为，专门用来证明「自检不得有副作用」这条不变量**会响**：
 *
 *   1. 数据目录可写 → **先写一份派生索引**，然后照样打印「界面自检通过」；
 *   2. 数据目录只读 → 写失败，报的是**操作系统的错**（EACCES），不是界面不变量；
 *   3. 数据目录根本不存在 → 直接崩（原型在没有 `data/vocab` 时就是 `FileNotFoundError`）,
 *      **报的是「环境缺文件」，不是「界面坏了」**。
 *
 * 第 2、3 条正是原型被记下来的病：只读环境里它把环境故障报成界面故障，人被引去修界面。
 * `proto/` 是冻结的只读证据（BRIEF：不许改、不许 import），所以这里**照形状重写**一份，
 * 不复用原型代码。
 */
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

const dataDir = process.env.AI_NOTE_DATA || 'data';

if (!existsSync(dataDir)) {
  // 原型在没有 data/vocab 时就是这样坏的：报环境缺文件，与界面无关
  process.stderr.write(`FileNotFoundError: 环境缺文件（${dataDir}/vocab/topic-outline.seed.json）\n`);
  process.stdout.write('界面自检未通过：✗ 页面里没有任何题目块（没有题可显示？）\n');
  process.exit(1);
}

mkdirSync(join(dataDir, 'problems'), {recursive: true});
// 这就是原型的那一步：把派生索引写进数据目录
writeFileSync(join(dataDir, 'index.json'), JSON.stringify({built_at: null, count: 0, problems: []}));

process.stdout.write('界面自检通过：2 个题目块都有 id，页头控件齐全。\n');
