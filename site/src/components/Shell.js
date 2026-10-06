import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {fetchIndex} from '../lib/api';
import {IndexContext, WorkbenchContext, useIndex, useWorkbench} from '../lib/shell-context';
import FailurePanel from './FailurePanel';
import Sidebar from './Sidebar';
import Workbench from './Workbench';

/**
 * 应用外壳（`Shell.js`）分两块，**取索引的那一块必须挂在路由之上**：
 *
 *   · `ShellProvider`：运行时取**一次** `GET /api/index`，用 React context 提供
 *     （`useIndex()`），并管着全屏工作台的开关（`useWorkbench()`）。
 *   · `Shell`（默认导出）：左侧栏 + 主内容区 + 索引横幅，是每一页的外壳。
 *
 * **为什么这两块不在一起**：外壳与页面是同一条链上的父子，而 React 的 context
 * 只向下流。页面（首页／科目下四栏／阅读页）是用 `<Layout>` 把外壳**套在自己外面**的，
 * 也就是说页面在 context provider 的**上面**——provider 若写在外壳里，页面永远读到
 * 默认值（实测过一次：侧栏画出来了、正文永远停在「正在读索引…」）。
 * 所以 provider 挂在 `src/theme/Root.js`（路由之上、每一页之外），
 * `Shell` 与页面都从它读同一份索引，**一次请求、一个真源**。
 *
 * 三条纪律：
 *   1. **一个页面只取一次索引**：`/api/index` 只在 `ShellProvider` 里取一次
 *      （索引是运行时数据，ADR 0001：数据变更绝不触发站点重建）。
 *   2. **不许静默**：信封里的 `warnings[]` 与 `skipped[]` **必须**显示，而且照原话
 *      （ADR 0007 第 6 条）。横幅就算一条都没有也在，并把「0 条」写出来——
 *      「没有警告」与「没查警告」长得一样是这个项目最怕的一类失败。
 *   3. 失败时给的是**服务自己说的话**，界面不重拼一句。
 *
 * `useIndex` / `useWorkbench` 从 `lib/shell-context.js` 转出去（本体为什么住在那儿，
 * 见那个文件的开头）：这样 `RedoPage` 能在**没有外壳**的离线渲染自检里照跑不误。
 */
export {useIndex, useWorkbench} from '../lib/shell-context';

/** 索引与工作台的那一层。挂在路由之上（`src/theme/Root.js`）。 */
export function ShellProvider({apiBase, children}) {
  const [state, setState] = useState({phase: 'loading', envelope: null, data: null, error: null});
  // 全屏工作台：`null` = 关着；`{pageId: null}` = 录一页新的；`{pageId: '…'}` = 改已有的一页
  const [workbench, setWorkbench] = useState(null);

  const reload = useCallback(async () => {
    setState({phase: 'loading', envelope: null, data: null, error: null});
    try {
      const envelope = await fetchIndex(apiBase);
      setState({phase: 'ready', envelope, data: envelope.data, error: null});
    } catch (error) {
      // 绝不 `catch {}`：服务的原话、hint 与码一路带到界面上
      setState({phase: 'failed', envelope: null, data: null, error});
    }
  }, [apiBase]);

  useEffect(() => {
    reload();
  }, [reload]);

  const index = useMemo(() => ({...state, reload}), [state, reload]);
  const workbenchApi = useMemo(
    () => ({open: (pageId = null) => setWorkbench({pageId}), close: () => setWorkbench(null)}),
    [],
  );

  return (
    <IndexContext.Provider value={index}>
      <WorkbenchContext.Provider value={workbenchApi}>
        {children}
        {workbench && (
          <Workbench
            apiBase={apiBase}
            pageId={workbench.pageId}
            subjects={state.data?.subjects || []}
            onClose={workbenchApi.close}
          />
        )}
      </WorkbenchContext.Provider>
    </IndexContext.Provider>
  );
}

/** 每一页的外壳：左侧栏 + 主内容区。索引从 `ShellProvider` 读（只取一次）。 */
export default function Shell({children}) {
  const index = useIndex();
  const workbench = useWorkbench();

  return (
    <div className="ai-note-shell">
      <Sidebar onUpload={() => workbench?.open(null)} />
      <div className="ai-note-shell__main">
        <IndexNotices index={index} />
        {children}
      </div>
    </div>
  );
}

/**
 * 索引级的三态横幅。
 *
 * 读不到时给**重试**按钮与服务的原话；读到了就把 `warnings[]` / `skipped[]`
 * 一条不落地摆出来（收在一个可折叠的 `<details>` 里，但**必须在**）。
 */
function IndexNotices({index}) {
  if (!index || index.phase === 'loading') {
    return (
      <p className="ai-note-meta" data-status="loading">
        正在从服务读索引…
      </p>
    );
  }

  if (index.phase === 'failed') {
    return (
      <FailurePanel
        title="读不到索引"
        error={index.error}
        onRetry={index.reload}
        retryLabel="重试（再读一次 /api/index）"
      />
    );
  }

  const warnings = index.envelope?.warnings || [];
  const skipped = index.envelope?.skipped || [];

  return (
    <details className="ai-note-banner ai-note-notices alert alert--warning"
             data-index-notices="true" open={skipped.length > 0}>
      <summary data-warnings-count={warnings.length} data-skipped-count={skipped.length}>
        索引里的警告 <strong>{warnings.length}</strong> 条 · 没能建出来的记录{' '}
        <strong>{skipped.length}</strong> 条（点开看原话）
      </summary>
      {/* 索引级警告与逐卡警告都在这一份平铺列表里（契约 §3）。 */}
      <ul data-notice="warnings">
        {warnings.length === 0 ? (
          <li className="ai-note-meta">服务这一趟没有报警告。</li>
        ) : (
          warnings.map((warning, i) => (
            <li
              key={`${warning.code || 'warning'}-${warning.id || ''}-${i}`}
              className={warning.level === 'hint' ? 'ai-note-hint' : 'ai-note-warn'}
              data-warning-code={warning.code}
              data-level={warning.level || 'warning'}>
              {warning.message}
              {warning.id ? <span className="ai-note-meta">（{warning.id}）</span> : null}
              {warning.code ? <span className="ai-note-meta">〔{warning.code}〕</span> : null}
            </li>
          ))
        )}
      </ul>
      {/* `skipped` 比警告重：这些卡**根本没建出来**，少一张不能只靠一句警告带过。 */}
      <ul data-notice="skipped">
        {skipped.length === 0 ? (
          <li className="ai-note-meta">服务这一趟没有跳过任何记录。</li>
        ) : (
          skipped.map((entry, i) => (
            <li
              key={`${entry.code || 'skipped'}-${entry.id || ''}-${i}`}
              className="ai-note-warn"
              data-skipped-code={entry.code}>
              {entry.message}
              {entry.id ? <span className="ai-note-meta">（{entry.id}）</span> : null}
              {entry.code ? <span className="ai-note-meta">〔{entry.code}〕</span> : null}
            </li>
          ))
        )}
      </ul>
    </details>
  );
}
