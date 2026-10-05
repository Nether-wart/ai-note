import React from 'react';
import Layout from '@theme/Layout';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {useLocation} from '@docusaurus/router';
import SplitEditor from '../components/SplitEditor';

/**
 * `/split?page=<页 id>`（#14 切分修正界面）。
 *
 * 路由只做两件事：把服务地址交给组件、从 URL 里取**页 id**。数据**在运行时取**
 * （ADR 0001：改一页绝不触发站点重建），所以构建产物里没有任何一页的内容。
 *
 * 页 id 缺了要**明确失败**，不许静默挑一页（那正是「改错了页」这类事故的开头）。
 */
function readPageId(search) {
  const params = new URLSearchParams(search || '');
  const pageId = (params.get('page') || '').trim();
  if (!pageId) return null;
  return pageId;
}

export default function Split() {
  const {siteConfig} = useDocusaurusContext();
  const location = useLocation();
  const pageId = readPageId(location?.search);

  return (
    <Layout title="切分修正" description="拖边界、合并、拆分、丢弃、改题型与题号；每次修正写回页文件">
      <main className="container margin-vert--lg">
        <h1>切分修正</h1>
        <p className="ai-note-meta">
          界面上画着两套坐标：整页照片上是**块框**，题面裁剪图上是**掩膜框**。
          混用会静默错位，所以换算只有一处实现。
        </p>
        {pageId
          ? <SplitEditor apiBase={siteConfig.customFields.apiBase} pageId={pageId} />
          : (
            <div className="ai-note-banner" role="alert" data-status="failed" data-error-code="page_id_missing">
              <strong>没给页 id</strong>
              <p>这一页要指名道姓地打开一页：<code>{'/split?page=<页 id>'}</code></p>
              <p className="ai-note-meta">
                为什么明确失败：猜一页会让你在**另一页**上动刀——那正是这个项目最怕的事故。
              </p>
            </div>
          )}
      </main>
    </Layout>
  );
}
