/**
 * `@docusaurus/router` 的**桩**（#8 的渲染自检专用）。
 *
 * `RedoPage` 用 `useLocation()` 拿 `?queue=…&i=…`。自检要脱网、脱浏览器地渲出这一页，
 * 就得给这个 import 一个替身——`ssr.mjs` 的 resolve 钩子把 `@docusaurus/router`
 * 指到这里，`search` 由 `globalThis.__REDO_SEARCH__` 注入。
 *
 * 被检查的是**渲染与队列逻辑**，不是路由本身（路由怎么解析 URL 是 Docusaurus 的事），
 * 所以这里替成桩不丢覆盖。自检会把自己做过什么说清楚（ADR 0007 第 6 条）。
 */
export function useLocation() {
  return {
    pathname: '/redo',
    search: globalThis.__REDO_SEARCH__ || '',
    hash: '',
    state: null,
    key: 'selftest',
  };
}

export const Link = 'a';
