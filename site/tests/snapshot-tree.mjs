/**
 * 目录快照（#8 的不变量 4）：把一棵树拍成 `{路径 → {size, mtime_ns}}`。
 *
 * 只**采**不**判**：判断在 `invariants.mjs` 的 `checkNoSideEffects` 里（一处实现）。
 * 采集必须由跑检查的那一侧做——在**真跑子进程的前后各来一次**，否则查的就不是
 * 「这次自检动没动东西」。所以这个文件既能当模块用（`selftest.mjs` 自己那一趟），
 * 也能当命令用（pytest 桥在子进程外面拍前后两张）：
 *
 *     node site/tests/snapshot-tree.mjs <目录> [<目录> …]   # 拍一个 JSON 到 stdout
 *
 * **只读**：它自己不写任何东西（拍了就要比，写了就比不出别人写没写）。
 */
import {readdirSync, statSync} from 'node:fs';
import {join, relative, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const SKIP_DIRECTORIES = new Set([
  'node_modules', '.git', '.npm-cache', 'build', '.docusaurus',
  '.worktrees', '.verify', 'runs', 'dist',
  '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
]);

/**
 * @param {string[]} roots 要拍的目录（相对或绝对）。不存在的目录**记进 `missing`**，
 *   不抛——"目录本来就没有"本身是自检要能说清的一件事（`data/` 不在 worktree 里）。
 * @returns {{files: Record<string, {size: number, mtime_ns: number}>, missing: string[]}}
 */
export function snapshotTree(roots) {
  const files = {};
  const missing = [];

  for (const root of roots) {
    const absolute = resolve(root);
    let entries;
    try {
      entries = walk(absolute);
    } catch (error) {
      if (error.code === 'ENOENT') {
        missing.push(absolute);
        continue;
      }
      throw error;
    }
    for (const path of entries) {
      const stat = statSync(path);
      files[relative(process.cwd(), path)] = {size: stat.size, mtime_ns: Number(stat.mtimeMs * 1e6)};
    }
  }

  return {files, missing: missing.sort()};
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir, {withFileTypes: true})) {
    if (entry.isDirectory()) {
      if (SKIP_DIRECTORIES.has(entry.name)) {
        continue;
      }
      walk(join(dir, entry.name), out);
    } else if (entry.isFile()) {
      out.push(join(dir, entry.name));
    }
  }
  return out;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const roots = process.argv.slice(2);
  if (roots.length === 0) {
    process.stderr.write('用法：node site/tests/snapshot-tree.mjs <目录> [<目录> …]\n');
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(snapshotTree(roots)));
}
