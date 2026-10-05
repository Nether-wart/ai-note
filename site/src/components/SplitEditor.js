import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {fetchPage, pageImageUrl, patchPage, resegmentPage} from '../lib/api';
import {
  EDIT_ACTIONS,
  PROBLEM_TYPES,
  blockBoxToDisplay,
  editOutcome,
  editPayload,
  editsRequest,
  keepLabel,
  pageView,
  resegmentCardWarnings,
  resegmentRows,
  resegmentSummary,
} from '../lib/split';

/**
 * 切分修正页（#14）。
 *
 * 三件事按 spec #2 的四个动作接在这个组件上：
 *
 *   1. **修正 → 写回页文件**（验收 3）。这一页自己不「记着」块列表：它把 `edits`
 *      发给 `PATCH /api/page/<id>`，然后**用服务返回的页**刷新显示。所以界面内存
 *      从来不是真相——页文件才是（spec #2 的立身之本：切分结果一旦只在界面内存里，
 *      「漏了一题」就永远查不出来）。
 *   2. **重切 → 逐块三态**（验收 1）。`POST …/resegment` 回来后**逐块显示
 *      「保留／新增」**与三档计数，而不是只说一句「重切完成」——否则「已入库的块
 *      被替换了」在界面上看不见，而那正是这条验收要防的。
 *   3. **两套坐标各画各的**（D5 的坑）。整页照片上画的是**块框**（整页坐标，
 *      只做缩放）；题面裁剪图上画的是**掩膜框**（裁剪图坐标）。要「把掩膜画到
 *      整页上」必须走 `maskBoxOntoPage` 换算——混用会静默错位。
 *
 * 一条纪律（#8 同源）：**把服务给的 `warnings[]` 原话显示出来**，一条都不吞
 * （ADR 0007 第 6 条）。取舍规则与「能不能自动判定」都不在这一页判——那是
 * `server/intake.py`（#12）与 `server/autojudge.py` 的活。
 *
 * `pageData` 是**给不靠浏览器的渲染自检留的缝**（#8 的做法）：给它一份页，这一页
 * 就不去 fetch，于是能在不联网、不起服务的前提下被 `renderToStaticMarkup` 渲染。
 */
export default function SplitEditor({apiBase, pageId, pageData = null}) {
  const [state, setState] = useState(() => ({
    phase: pageData ? 'ready' : 'loading',
    page: pageData,
    error: null,
  }));
  const [last, setLast] = useState(null);          // 上一次修正/重切的结果
  const [resegment, setResegment] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (pageData) return;
    setState({phase: 'loading', page: null, error: null});
    try {
      const envelope = await fetchPage(apiBase, pageId);
      setState({phase: 'ready', page: envelope.data.page, error: null});
    } catch (error) {
      setState({phase: 'failed', page: null, error});
    }
  }, [apiBase, pageId, pageData]);

  useEffect(() => {
    load();
  }, [load]);

  const view = useMemo(() => pageView(state.page || {blocks: []}), [state.page]);
  const rows = useMemo(() => resegmentRows(resegment), [resegment]);
  const summary = useMemo(() => resegmentSummary(resegment), [resegment]);
  const cardWarnings = useMemo(() => resegmentCardWarnings(resegment), [resegment]);

  // 发一串修正给服务，然后**用服务返回的页**刷新（界面内存不是真相）
  const sendEdits = useCallback(async (edits, {dryRun = false} = {}) => {
    setBusy(true);
    setLast(null);
    try {
      const envelope = await patchPage(apiBase, pageId, editsRequest(edits, {dryRun}));
      const report = envelope.data;
      setLast({...editOutcome(report), envelopeWarnings: envelope.warnings || []});
      if (!dryRun && report.page) setState({phase: 'ready', page: report.page, error: null});
    } catch (error) {
      setLast({failed: true, error});
    } finally {
      setBusy(false);
    }
  }, [apiBase, pageId]);

  const doResegment = useCallback(async () => {
    setBusy(true);
    setResegment(null);
    try {
      const envelope = await resegmentPage(apiBase, pageId);
      setResegment({...envelope.data, warnings: envelope.warnings || []});
    } catch (error) {
      setResegment({failed: true, error});
    } finally {
      setBusy(false);
    }
  }, [apiBase, pageId]);

  const run = (action, fields) => {
    let edit;
    try {
      edit = editPayload(action, fields);
    } catch (error) {
      setLast({failed: true, error});
      return;
    }
    sendEdits([edit]);
  };

  if (state.phase === 'loading') {
    return <p data-status="loading">正在从服务读这一页…</p>;
  }

  if (state.phase === 'failed') {
    return (
      <div className="ai-note-banner" role="alert" data-status="failed" data-error-code={state.error?.code}>
        <strong>读不到这一页</strong>
        <p>{state.error?.message}</p>
        {state.error?.hint && <p className="ai-note-meta">怎么办：{state.error.hint}</p>}
        <button type="button" onClick={load}>重试</button>
      </div>
    );
  }

  return (
    <div className="ai-note-split" data-page-id={view.page_id}>
      <p className="ai-note-meta" data-page-path>
        块 {view.counts.blocks} 块 · 收 {view.counts.kept} / 不收 {view.counts.dropped} / 待定 {view.counts.pending}
        {view.origin?.original_file ? ` · 来自 ${view.origin.original_file}` : ''}
      </p>

      {/* 整页照片：上面画的是**块框**（整页坐标）。掩膜要画到这里必须换算。 */}
      <figure className="ai-note-page-photo">
        <img src={pageImageUrl(apiBase, view.page_id)} alt="整页照片（上面画的是切出来的块框）"
             data-image-kind="page" />
        <figcaption className="ai-note-meta" data-coordinate-basis="page">
          块框画在整页照片上（整页坐标，就是页文件里的 bbox_norm）
        </figcaption>
      </figure>
      <p className="ai-note-meta" data-coordinate-basis="crop">
        题面裁剪图上的掩膜框是另一套坐标（clean.boxes_norm／manual）；
        要把掩膜画到上面那张整页照片上，必须换算（`maskBoxOntoPage`）——混用会静默错位。
      </p>

      <div className="ai-note-split-actions">
        <button type="button" disabled={busy} onClick={doResegment} data-action="resegment">
          重切这一页
        </button>
      </div>

      {resegment?.failed && (
        <div className="ai-note-banner" role="alert" data-error-code={resegment.error?.code}>
          <strong>重切没跑成</strong>
          <p>{resegment.error?.message}</p>
          {resegment.error?.hint && <p className="ai-note-meta">怎么办：{resegment.error.hint}</p>}
        </div>
      )}

      {resegment && !resegment.failed && (
        <section className="ai-note-summary" data-resegment data-ran={String(summary.ran)}>
          <h2>重切对账</h2>
          {summary.segmentation_unavailable ? (
            <p data-segmentation="unavailable">
              这一趟没有可用的切分（服务没接上切分那一层）→ 没有块可以对照。
              <strong>这里不显示假的块列表。</strong>
            </p>
          ) : (
            <>
              <p data-resegment-counts>{summary.line}</p>
              <ul className="ai-note-resegment-rows">
                {rows.map((row) => (
                  <li key={row.block_id} data-block-id={row.block_id} data-state={row.state}>
                    <strong>{row.label}</strong>
                    <span className="ai-note-meta">
                      {` 块 ${row.block_id} · ${row.detail}`}
                      {row.card_id ? ` · 卡片 ${row.card_id}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
              {summary.needs_human && (
                <p data-needs-human>服务说这一趟要人看一眼（有块绑着人动过的卡，或块消失了）。</p>
              )}
            </>
          )}
          {cardWarnings.map((warning) => (
            <p key={warning.message} className="ai-note-meta" data-warning-code={warning.code}>
              {warning.message}
            </p>
          ))}
        </section>
      )}

      {last?.failed && (
        <div className="ai-note-banner" role="alert" data-error-code={last.error?.code}>
          <strong>这次修正没成</strong>
          <p>{last.error?.message}</p>
          {last.error?.hint && <p className="ai-note-meta">怎么办：{last.error.hint}</p>}
        </div>
      )}

      {last && !last.failed && (
        <section className="ai-note-summary" data-edit-outcome data-changed={String(last.changed)}>
          <h2>上一次修正</h2>
          <p data-edit-counts>
            {last.changed
              ? `改动了 ${last.applied} 条${last.noop ? `，另有 ${last.noop} 条没动` : ''}`
              : '一条都没改动（幂等重复提交，或点的块本来就是这个样子）'}
          </p>
          <p className="ai-note-meta">
            {last.wrote_page ? '已写回页文件' : '页文件没写（预演）'}
            {' · '}
            {last.wrote_cards ? '写了题卡' : '没写题卡（题卡归「入库」动作）'}
          </p>
          <ul>
            {last.rows.map((row) => (
              <li key={`${row.index}-${row.action}`} data-edit-action={row.action}>
                {row.label}：{row.changed ? '已改' : '没动'}
                {row.blocks_changed.length ? `（块 ${row.blocks_changed.join('、')}）` : ''}
              </li>
            ))}
          </ul>
          {last.warnings.map((warning) => (
            <p key={warning.message} className="ai-note-meta" data-warning-code={warning.code}
               data-warning-level={warning.level}>
              {warning.message}
            </p>
          ))}
        </section>
      )}

      <section>
        <h2>块</h2>
        <ul className="ai-note-block-list">
          {view.blocks.map((row) => (
            <li key={row.id} className="ai-note-card" data-block-id={row.id}
                data-keep={row.keep === null ? 'pending' : String(row.keep)}>
              <div className="ai-note-badges">
                <span className="ai-note-badge">块 {row.id}</span>
                <span className="ai-note-badge">{row.keep_cn}</span>
                {row.problem_type_cn && <span className="ai-note-badge">{row.problem_type_cn}</span>}
                {row.question_no !== null && (
                  <span className="ai-note-badge">第 {row.question_no} 题</span>
                )}
                {row.card_id
                  ? <span className="ai-note-badge">{row.card_id}</span>
                  : <span className="ai-note-badge ai-note-badge--paper">还没入库</span>}
              </div>

              {row.decision?.reason && (
                <p className="ai-note-meta" data-decision-rule={row.decision.rule}>
                  {row.decision.reason}
                </p>
              )}
              {row.ink && (
                <p className="ai-note-meta">
                  {`红笔 ${row.ink.colored_px}px（占 ${(100 * (row.ink.colored_ratio || 0)).toFixed(1)}%）`}
                </p>
              )}

              <div className="ai-note-split-actions">
                <button type="button" disabled={busy}
                        onClick={() => run(row.keep === true ? 'drop' : 'keep', {block_id: row.id})}
                        data-action={row.keep === true ? 'drop' : 'keep'}>
                  {row.keep === true ? '丢弃这一块' : '收进库'}
                </button>
                <button type="button" disabled={busy}
                        onClick={() => run('question_no', {block_id: row.id, question_no: null})}
                        data-action="question_no-clear">
                  题号看不清
                </button>
                {Object.keys(PROBLEM_TYPES).map((type) => (
                  <button key={type} type="button" disabled={busy}
                          onClick={() => run('type', {block_id: row.id, problem_type: type})}
                          data-action={`type-${type}`}>
                    {PROBLEM_TYPES[type]}
                  </button>
                ))}
              </div>

              {/* 掩膜框画在**题面裁剪图**上（裁剪图坐标）；这一段在整页照片上必须换算 */}
              {row.masks_in_crop.length > 0 && (
                <p className="ai-note-meta" data-mask-count={row.masks_in_crop.length}>
                  {`这一块有 ${row.masks_in_crop.length} 个手写掩膜框`}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>没入库的题</h2>
        {view.not_kept.length === 0
          ? <p className="ai-note-meta">这一页的块全都入库了。</p>
          : (
            <ul data-not-kept>
              {view.not_kept.map((row) => (
                <li key={row.block_id} data-block-id={row.block_id} data-keep={row.keep === null ? 'pending' : String(row.keep)}>
                  {`块 ${row.block_id}：${row.keep_cn}`}
                  {row.reason ? ` —— ${row.reason}` : ''}
                </li>
              ))}
            </ul>
          )}
      </section>
    </div>
  );
}

/** 七条动作的清单（自检对着它断言「最小集合」一条不缺、也没多）。 */
export const SUPPORTED_ACTIONS = Object.keys(EDIT_ACTIONS);
