/**
 * 应用外壳（`components/Shell.js`）与它的页面之间那条缝：**索引**与**工作台**。
 *
 * 为什么 context 本体住在这里、而不是写在 `Shell.js` 里：`RedoPage` 也要读外壳取回来的
 * 索引（一个页面只取一次索引），而 `site/tests/selftest.mjs` 会把 `RedoPage` **单独**
 * 渲出来——那一趟没有外壳、也没有浏览器。若 `RedoPage` 去 import `Shell.js`，
 * 就会把 `@docusaurus/useDocusaurusContext`、全屏工作台这些只属于浏览器的东西
 * 一起拖进离线的渲染自检，自检当场跑不起来。这个模块只 import `react`，
 * 于是两条路都能走：有外壳时读外壳的，没有外壳时 `useIndex()` 给 `null`。
 *
 * 一条纪律：`useIndex()` **在没有外壳时返回 `null`，不抛**。理由是上面那件事——
 * 「这一趟没有外壳」是一个正常状态（渲染自检），不是一个错误。
 */
import {createContext, useContext} from 'react';

/**
 * 索引读数的形状（`Shell.js` 提供）：
 *
 *   `{phase: 'loading' | 'failed' | 'ready', envelope, data, error, reload}`
 *
 * `envelope` 是服务的整封信（`warnings[]` / `skipped[]` 在里面，**不许静默**）；
 * `data` 就是 `/api/index` 的 `data`（`problems` / `stats` / `subjects` / `outline` /
 * `briefs` / `screen_redo`）。`reload()` 是重试。
 */
export const IndexContext = createContext(null);

/** 工作台的开关：`{open(pageId), close}`。首页与细则上的「上传」按钮走它。 */
export const WorkbenchContext = createContext(null);

/** 读外壳给的索引读数。没有外壳时是 `null`（不是抛）。 */
export function useIndex() {
  return useContext(IndexContext);
}

/** 读工作台的开关。没有外壳时是 `null`。 */
export function useWorkbench() {
  return useContext(WorkbenchContext);
}
