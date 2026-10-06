import React from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {ShellProvider} from '@site/src/components/Shell';

/**
 * 应用外壳的**提供者那一层**，挂在路由之上（Docusaurus 的 `@theme/Root` 是包住整个
 * 应用的那一层）。
 *
 * 为什么非得是这一层：React 的 context 只向下流，而页面是**用 `<Layout>` 把外壳套在
 * 自己外面**的——页面在外壳的上面。索引若在外壳里取、在外壳里提供，页面就永远读到
 * 默认值（实测过一次：侧栏画出来了，正文永远停在「正在读索引…」）。所以索引与工作台
 * 的开关提到这里，`Shell`（每一页的外壳）与页面读的是同一份。
 *
 * 这一层只做一件事：把服务地址交给 `ShellProvider`。别的什么都不动。
 */
export default function Root({children}) {
  const {siteConfig} = useDocusaurusContext();
  return <ShellProvider apiBase={siteConfig.customFields.apiBase}>{children}</ShellProvider>;
}
