import {useEffect, useState} from 'react';

/**
 * 「已经挂到浏览器上了吗」——地址里的**查询串**要等它之后才读。
 *
 * 为什么非要有这么一个小东西：本项目一律走查询串风格的路由（`/problem?pid=…`，
 * ADR 0006 那处待验证项），而静态站**每个路由只有一份 HTML**——它是按**没有查询串**
 * 那条地址渲出来的（`/problem/index.html`）。首次渲染若直接读 `useLocation().search`，
 * 客户端与构建期渲出来的东西就不一致：React 报 hydrate 不匹配（错误 #418），
 * 然后**丢掉那份 HTML 重渲整棵子树**，控制台上留一条红字。
 *
 * 所以查询串在 `mounted` 之后才读：构建期与**首次**渲染渲染的是同一句「正在打开…」。
 * 这不是 SSR（ADR 0006 明说不做 SSR），恰恰相反——它承认构建期渲不出这一页的内容，
 * 于是不假装渲得出来。
 */
export function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  return mounted;
}
