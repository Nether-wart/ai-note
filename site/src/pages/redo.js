import React from 'react';
import Layout from '@theme/Layout';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import RedoPage from '../components/RedoPage';

/**
 * `/redo?queue=<题目列表>&i=<第几题>`（#7）。
 *
 * 路由只做一件事：把站点配置里的服务地址交给 `RedoPage`。数据**在运行时取**
 * （ADR 0001），所以构建产物里没有任何一道题的内容；SSR 阶段只渲染「正在读…」与
 * （没有 queue 参数时的）显式失败面板。
 */
export default function Redo() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title="屏幕重做"
      description="队列在 URL 里；题面是擦除手写后的图；判定由服务做">
      <main className="container margin-vert--lg">
        <h1>屏幕重做</h1>
        <RedoPage apiBase={siteConfig.customFields.apiBase} />
      </main>
    </Layout>
  );
}
