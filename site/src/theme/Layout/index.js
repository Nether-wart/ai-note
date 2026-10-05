import React from 'react';
import Layout from '@theme-original/Layout';
import Shell from '@site/src/components/Shell';

/**
 * 把每一页的正文套进应用外壳（swizzle 的 **wrap** 形式）。
 *
 * 为什么必须走这一条路：索引栏要在**每一页**都在（首页、科目下四栏、阅读页、重做页、
 * 切分那一页），而 docs 插件在本项目里是**关掉的**（ADR 0009：索引是运行时数据，
 * 进构建产物就与 ADR 0001 冲突），所以主题没有文档侧栏可用；`Layout` 是唯一被所有
 * 路由共用的那一层。于是这里只做一件事——把 `@theme-original/Layout` 的 children
 * 交给 `Shell`：`Shell` 画左侧栏与主内容区，索引本身由 `ShellProvider` 提供
 * （它挂在路由之上，见 `src/theme/Root.js`——context 只向下流，页面在外壳的上面）。
 *
 * 只改这一层，别的什么都不动：导航栏、页脚、`baseUrl` 处理仍归原来的 `Layout`。
 */
export default function LayoutWrapper(props) {
  return (
    <Layout {...props}>
      <Shell>{props.children}</Shell>
    </Layout>
  );
}
