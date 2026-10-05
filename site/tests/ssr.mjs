/**
 * 给自检用的 **SSR 台子**（#8）：把真组件渲成 HTML，**不开浏览器、不联网**。
 *
 * 为什么值得这么做：界面不变量的判据吃的是 HTML，而"喂给判据的 HTML 从哪来"决定了
 * 这些检查值不值钱。手写的 HTML 只能证明判据自己会算；**真组件渲出来的 HTML**
 * 才能证明「题面被换成原图」这类回归会被抓住。所以这里用 React 自己的
 * `renderToStaticMarkup`（React 19 的 `react-dom/server`）跑一次真渲染。
 *
 * 三件事说清楚（ADR 0007 第 6 条：不许静默）：
 *   1. **JSX 就地转**：`site/src/**` 与 `site/tests/**` 的 `.js` 用 `@babel/preset-react`
 *      过一道（走 node 的 `registerHooks`），不落盘、不建缓存。浏览器能跑的 JSX，
 *      这里原样跑——**不复制一份组件的实现**。
 *   2. **`@docusaurus/router` 换成桩**（`fixtures/router-stub.js`），只注入 `search`。
 *   3. **不要 `site/node_modules` 就没有这一趟**：那时抛 `HarnessUnavailable`，
 *      调用方必须报"这次没跑完"，**绝不许报"界面没问题"**——原型把环境故障
 *      报成界面故障，正是 #8 要消灭的病。
 */
import {readFileSync} from 'node:fs';
import {createRequire, registerHooks} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

/** 环境缺依赖（没装 `site/node_modules`）：这**不是**界面坏了。 */
export class HarnessUnavailable extends Error {
  constructor(message) {
    super(message);
    this.name = 'HarnessUnavailable';
  }
}

const SITE_ROOT = new URL('../', import.meta.url);
const SRC = new URL('src/', SITE_ROOT).href;
const TESTS = new URL('tests/', SITE_ROOT).href;

/**
 * JSX → JS 就地转（`@babel/preset-react` 的 automatic runtime）。
 *
 * preset **按绝对路径**给：babel 按名字找 preset 时会从自己的 `cwd` 往上找
 * `node_modules`，而自检可能从仓库根被调用（那里没有 `node_modules`）——
 * 于是「从仓库根跑」与「从 site/ 跑」会得到两种结果。按路径给就与 cwd 无关了。
 */
function transformJsx(source, filename, transformSync, presetReact) {
  const out = transformSync(source, {
    filename,
    presets: [[presetReact, {runtime: 'automatic'}]],
    sourceType: 'module',
    babelrc: false,
    configFile: false,
  });
  return out.code;
}

/**
 * 起台子：装钩子、转 JSX、把两个真组件 import 进来。
 *
 * @returns {Promise<{renderQuestion: Function, renderPage: Function, renderResult: Function,
 *                    renderBroken: Function, note: string}>}
 */
export async function createHarness() {
  let transformSync;
  let renderToStaticMarkup;
  let createElement;
  let presetReact;
  try {
    ({transformSync} = await import('@babel/core'));
    ({renderToStaticMarkup} = await import('react-dom/server'));
    ({createElement} = await import('react'));
    presetReact = createRequire(import.meta.url).resolve('@babel/preset-react');
  } catch (error) {
    throw new HarnessUnavailable(
      `渲染自检要 site/node_modules（@babel/core、react-dom），现在缺：${error.message}`,
    );
  }

  const routerStub = new URL('tests/fixtures/router-stub.js', SITE_ROOT).href;

  registerHooks({
    resolve(specifier, context, nextResolve) {
      const parent = context.parentURL || '';
      const ours = parent.startsWith(SRC) || parent.startsWith(TESTS);
      if (ours && specifier === '@docusaurus/router') {
        return {url: routerStub, shortCircuit: true};
      }
      // 组件之间是 `../lib/redo` 这种不带扩展名的写法（打包器会补），node 不会
      if (ours && specifier.startsWith('.') && !/\.[cm]?js$/.test(specifier)) {
        return nextResolve(`${specifier}.js`, context);
      }
      return nextResolve(specifier, context);
    },
    load(url, context, nextLoad) {
      if ((url.startsWith(SRC) || url.startsWith(TESTS)) && url.endsWith('.js')) {
        const source = readFileSync(new URL(url), 'utf8');
        return {
          format: 'module',
          source: transformJsx(source, fileURLToPath(url), transformSync, presetReact),
          shortCircuit: true,
        };
      }
      return nextLoad(url, context);
    },
  });

  const {default: SplitEditor} = await import('../src/components/SplitEditor.js');
  const {default: RedoQuestion} = await import('../src/components/RedoQuestion.js');
  const {default: RedoPage, ResultPanel} = await import('../src/components/RedoPage.js');
  const {default: RedoHeader} = await import('../src/components/RedoHeader.js');
  const broken = await import('./fixtures/broken.js');

  return {
    note: '真组件经 renderToStaticMarkup 渲染；@docusaurus/router 用桩注入 search；不落盘',
    /** 重做页的题面与作答区（不变量 1、2、5）。 */
    renderQuestion: (problem, {apiBase = ''} = {}) =>
      renderToStaticMarkup(
        createElement(RedoQuestion, {problem, apiBase, onSubmit: () => {}, submitting: false}),
      ),
    /** 整个重做页（不变量 3 的队列与失败面板）。`indexData` 是给自检留的缝。 */
    renderPage: ({search, indexData}) => {
      globalThis.__REDO_SEARCH__ = search;
      try {
        return renderToStaticMarkup(createElement(RedoPage, {apiBase: '', indexData}));
      } finally {
        delete globalThis.__REDO_SEARCH__;
      }
    },
    /**
     * 切分修正页（#14）。`pageData` 是给自检留的缝：给了它就不去 fetch，
     * 于是能在不联网、不起服务的前提下被渲染出来。
     */
    renderSplit: (pageData, {pageId = 'aaaabbbbcccc', apiBase = ''} = {}) =>
      renderToStaticMarkup(
        createElement(SplitEditor, {apiBase, pageId, pageData}),
      ),
    /** 同上，但**不给页**（渲染出「正在读…」那一刻）。 */
    renderSplitLoading: ({pageId = 'aaaabbbbcccc'} = {}) =>
      renderToStaticMarkup(createElement(SplitEditor, {apiBase: '', pageId})),
    /** 判定结果面板（R7：掌握读数按 verdict 分流）。`result` 是 `attemptResult` 的形状。 */
    renderResult: (result) =>
      renderToStaticMarkup(createElement(ResultPanel, {result})),
    /** 页头（裁决 D3 的 N/M 显示）。 */
    renderHeader: (header) => renderToStaticMarkup(createElement(RedoHeader, {header})),
    /** 故意做坏的那三个组件——自检的报警能力靠它们证明。 */
    broken: {
      ShowsOriginal: (problem, {apiBase = ''} = {}) =>
        renderToStaticMarkup(createElement(broken.ShowsOriginal, {problem, apiBase})),
      SolutionWithAnswerBox: (problem) =>
        renderToStaticMarkup(createElement(broken.SolutionWithAnswerBox, {problem})),
      LeaksAnswer: (problem) => renderToStaticMarkup(createElement(broken.LeaksAnswer, {problem})),
      FallsBackToFirst: ({queue, apiBase = ''}) =>
        renderToStaticMarkup(createElement(broken.FallsBackToFirst, {queue, apiBase})),
    },
  };
}
