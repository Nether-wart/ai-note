import React, {useCallback, useEffect, useState} from 'react';
import Layout from '@theme/Layout';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {fetchIndex} from '../lib/api';
import ProblemList from '../components/ProblemList';

/**
 * 清单页。
 *
 * **数据在运行时取**（ADR 0001：录入一道题绝不触发 Docusaurus 重建），所以这里是
 * 构建期渲染「正在读…」、浏览器里再 fetch——构建产物里没有一道题的内容。
 * 这也是 Tauri 兼容要求的那条：纯静态 bundle，不做 SSR。
 */
export default function Home() {
  const {siteConfig} = useDocusaurusContext();
  const apiBase = siteConfig.customFields.apiBase;
  const [state, setState] = useState({phase: 'loading', envelope: null, error: null});

  const load = useCallback(async () => {
    setState({phase: 'loading', envelope: null, error: null});
    try {
      const envelope = await fetchIndex(apiBase);
      setState({phase: 'ready', envelope, error: null});
    } catch (error) {
      setState({phase: 'failed', envelope: null, error});
    }
  }, [apiBase]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Layout title="清单" description="错题清单：运行时从只读服务取派生索引">
      <main className="container margin-vert--lg">
        <h1>错题清单</h1>
        <p className="ai-note-meta">
          数据来自 <code>{apiBase}</code>，运行时取——录入一道题不会触发站点重建。
        </p>

        {state.phase === 'loading' && (
          <p data-status="loading">正在从服务读索引…</p>
        )}

        {state.phase === 'failed' && (
          <div className="ai-note-banner" role="alert" data-status="failed">
            <strong>读不到索引</strong>
            <p>{state.error?.message}</p>
            {state.error?.hint && <p className="ai-note-meta">怎么办：{state.error.hint}</p>}
            <p className="ai-note-meta">
              错误码：<code>{state.error?.code}</code>／<code>{state.error?.reason}</code>
              {state.error?.status ? `（HTTP ${state.error.status}）` : ''}
            </p>
            <button type="button" onClick={load}>
              重试
            </button>
          </div>
        )}

        {state.phase === 'ready' && (
          <ProblemList envelope={state.envelope} apiBase={apiBase} />
        )}
      </main>
    </Layout>
  );
}
