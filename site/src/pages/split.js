import React from 'react';
import Layout from '@theme/Layout';
import {useLocation} from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import FailurePanel from '../components/FailurePanel';
import Workbench from '../components/Workbench';
import {useIndex} from '../components/Shell';
import {useMounted} from '../lib/use-mounted';

/**
 * `/split?page=<页 id>`：从细则点某一页进来重切。
 *
 * 它现在只是**同一个工作台的「改已有的一页」模式**（`Workbench` 给了 `pageId` 就是这一档）：
 * 照片、块框、拖边界／新增／删除／改去留／改题号题型、重置为预设、入库——一份实现，
 * 不在这里抄第二份。`pages/split.js` 只做两件事：从地址里取**页 id**、把服务地址与
 * 科目词表交给工作台。
 *
 * 页 id 缺了要**明确失败**，不许静默挑一页（那正是「在另一页上动刀」这类事故的开头）。
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
  // 查询串**挂载之后**才读：构建期那份 HTML 是按 `/split`（没有 `?page=`）渲的，
  // 首次渲染必须与它一致，否则 React 报 hydrate 不匹配（见 `lib/use-mounted.js`）。
  const mounted = useMounted();
  const pageId = mounted ? readPageId(location?.search) : null;
  // 科目词表跟着索引一起来（一个页面只取一次索引）：改已有的一页时它只用于显示
  const index = useIndex();
  const subjects = index?.data?.subjects || [];

  return (
    <Layout
      title="切分与录入"
      description="拖边界、新增、删除、改去留与题号题型；每一次修正都写回页文件">
      <main className="container margin-vert--lg" data-page="split">
        {!mounted ? (
          <p data-status="loading">正在打开这一页…</p>
        ) : pageId ? (
          <Workbench
            apiBase={siteConfig.customFields.apiBase}
            pageId={pageId}
            subjects={subjects}
          />
        ) : (
          <FailurePanel
            title="没给页 id"
            error={{
              code: 'page_id_missing',
              message: '这一页要指名道姓地打开一页：/split?page=<页 id>',
              hint: '为什么明确失败：猜一页会让你在**另一页**上动刀——那正是这个项目最怕的事故。',
            }}
            extra="要录一页新的，回首页点「上传整页照片」。"
          />
        )}
      </main>
    </Layout>
  );
}
