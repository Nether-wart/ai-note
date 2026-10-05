import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {useLocation} from '@docusaurus/router';
import {fetchIndex, postAttempt} from '../lib/api';
import RedoHeader from './RedoHeader';
import RedoQuestion from './RedoQuestion';
import {
  attemptResult,
  buildRedoUrl,
  creditLine,
  parseRedoQuery,
  redoHeader,
  removeAt,
  resolveQueueItem,
  submitScreenAnswer,
} from '../lib/redo';

/**
 * 屏幕重做页（#7）。
 *
 * 队列**只由 URL 带着**（验收 1，契约 §4）：`/redo?queue=p-a,p-b&i=1`。理由是硬的——
 * 判完一道题的瞬间它进了冷却、从默认打印清单里消失，服务端现算的「第 i+1 题」会漂到
 * 别处去。所以这一页不「重新取一批题」，只在判完之后把做过的那道**从 URL 的队列里拿掉**。
 *
 * 越界／队列里的题查不到／队列为空**一律明确失败**（spec #1 第 34 条）：原型那两处
 * 静默过滤（`proto/server.py:320-321` 的 `if pid in by_id`、`:933-936` 的 `continue`）
 * 一条都不许带进来。失败界面带 `data-error-code`，`#8` 就是对着它断言的。
 *
 * 判定**由服务做**（验收 4）：这一页只提交 `{channel:"screen", answer}`，然后把服务
 * 给的原话（`mastery.note`、`error.message`）显示出来，自己一个判定字段都不填。
 *
 * `indexData` 是**给不靠浏览器的渲染自检留的缝**（#8）：给它一份索引，这一页就不去
 * fetch，于是能在不联网、不起服务的前提下被 `renderToStaticMarkup` 渲染出来。
 * 没有它的时候行为与以前一模一样（运行时去服务取）。
 */
export default function RedoPage({apiBase, indexData = null}) {
  const location = useLocation();
  const search = location?.search || '';

  // URL 是队列的真源；`parsed` 一变（刷新、另一个窗口、清单页再点一次）就整页重来。
  const parsed = useMemo(() => parseRedoQuery(search), [search]);
  const [queue, setQueue] = useState(() => (parsed.ok ? parsed.queue : []));
  const [i, setI] = useState(() => (parsed.ok ? parsed.i : 0));
  const [result, setResult] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [indexState, setIndexState] = useState({phase: 'loading', envelope: null, error: null});

  useEffect(() => {
    setQueue(parsed.ok ? parsed.queue : []);
    setI(parsed.ok ? parsed.i : 0);
    setResult(null);
    setSubmitError(null);
  }, [parsed]);

  const load = useCallback(async () => {
    setIndexState({phase: 'loading', envelope: null, error: null});
    try {
      const envelope = await fetchIndex(apiBase);
      setIndexState({phase: 'ready', envelope, error: null});
    } catch (error) {
      setIndexState({phase: 'failed', envelope: null, error});
    }
  }, [apiBase]);

  useEffect(() => {
    // 自检注入了索引就不去取：渲染自检要能在**不联网、不起服务**的前提下跑
    if (!indexData) {
      load();
    }
  }, [load, indexData]);

  const data = indexData ?? (indexState.phase === 'ready' ? indexState.envelope.data : null);
  const header = useMemo(
    () => (data ? redoHeader(data, parsed.ok ? parsed.basis : null) : null),
    [data, parsed],
  );

  // 队列本身的失败（解析已经拦掉大部分；判完最后一道会走到 queue_empty）
  const queueError = !parsed.ok
    ? parsed
    : queue.length === 0
      ? {
          ok: false,
          code: 'queue_empty',
          message: '队列是空的：这一批题都做完了。',
          details: {param: 'queue'},
          hint: '回清单页再挑一批；刷新之后这个链接也是这个状态',
        }
      : i >= queue.length
        ? {
            ok: false,
            code: 'index_out_of_range',
            message: `第 ${i} 题越界：这个队列里只剩 ${queue.length} 道（索引 0..${queue.length - 1}）`,
            details: {param: 'i', value: i, count: queue.length},
            hint: '绝不悄悄落到别的题上：回清单页重新挑一批',
          }
        : null;

  // 队列里的题查不到 → 明确失败，不跳过它继续
  const resolution = data && !queueError ? resolveQueueItem(data.problems, queue, i) : null;

  const submit = useCallback(
    async (answer) => {
      const pid = queue[i];
      setSubmitting(true);
      setSubmitError(null);
      const outcome = await submitScreenAnswer({
        pid,
        answer,
        post: (targetPid, payload) => postAttempt(apiBase, targetPid, payload),
      });
      setSubmitting(false);

      if (!outcome.ok) {
        // 422（不能自动判定）／502（模型没问成）都照服务的原话显示，绝不自己编一句
        setSubmitError(outcome);
        return;
      }

      setResult({pid, ...attemptResult(outcome.envelope.data)});

      const next = removeAt(queue, i);
      if (!next.ok) {
        setSubmitError(next);
        return;
      }
      setQueue(next.queue);
      // URL 跟着走（刷新与另一个窗口看到的是「还没做的那些」），但不重算队列：
      // 只把刚判完的那道拿掉（第 21 条：顺序在 URL 里定住，不许突然换一批）。
      if (typeof window !== 'undefined') {
        window.history.replaceState(
          null,
          '',
          buildRedoUrl({queue: next.queue, i, basis: parsed.ok ? parsed.basis : null}),
        );
      }
    },
    [apiBase, queue, i, parsed],
  );

  return (
    <div
      data-redo-page="true"
      data-queue={queue.join(',')}
      data-queue-size={queue.length}
      data-queue-index={i}>
      <p className="ai-note-meta">
        队列在 URL 里（可刷新，也可以把这条链接开在另一个窗口）。本批还剩{' '}
        <strong data-queue-remaining={queue.length}>{queue.length}</strong> 道。题面是擦除手写后的图，
        判定由服务做——这一页只提交作答。
      </p>

      {indexState.phase === 'loading' && <p data-status="loading">正在从服务读索引…</p>}
      {indexState.phase === 'failed' && (
        <ErrorPanel title="读不到索引" error={indexState.error} onRetry={load} />
      )}

      {header && !header.known && (
        <ErrorPanel
          title="候选总体认不出"
          error={{
            code: 'basis_unknown',
            message: `服务算好的几套候选总体里没有这个：'${header.basis}'`,
            details: {available_bases: header.available_bases},
          }}
        />
      )}
      {header && header.known && <RedoHeader header={header} />}

      {result && <ResultPanel result={result} />}
      {submitError && <ErrorPanel title="这次作答没有被判定" error={submitError} />}

      {queueError && (
        <ErrorPanel
          title={queueError.code === 'index_out_of_range' ? '索引越界' : '队列不可用'}
          error={queueError}
        />
      )}
      {resolution && !resolution.ok && (
        <ErrorPanel title="队列里的题在索引里找不到" error={resolution} />
      )}
      {resolution && resolution.ok && (
        <RedoQuestion
          problem={resolution.problem}
          apiBase={apiBase}
          onSubmit={submit}
          submitting={submitting}
        />
      )}
    </div>
  );
}

/** 失败一律是一个显式的面板，带机器可读的 `data-error-code`（#8 照它断言）。 */
function ErrorPanel({title, error, onRetry}) {
  return (
    <div className="ai-note-banner" role="alert" data-status="failed" data-error-code={error?.code || 'unknown'}>
      <strong>{title}</strong>
      <p data-error-message="true">{error?.message}</p>
      {error?.hint && <p className="ai-note-meta">怎么办：{error.hint}</p>}
      {error?.details && Object.keys(error.details).length > 0 && (
        <p className="ai-note-meta">
          细节：<code>{JSON.stringify(error.details)}</code>
        </p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  );
}

/** 判定结果区：`note` 与掌握读数（契约 §10.1）都照服务给的原话。
 *
 * 具名导出是**给渲染测试的接缝**（`site/tests/redo-result-render.test.mjs` 用
 * `ssr.mjs` 的台子把它渲成 HTML）：面板里那两句读数必须按判定分流，而这件事
 * 只有把真组件渲出来才断言得了（R7）。
 */
export function ResultPanel({result}) {
  const credit = creditLine(result);
  return (
    <div
      className="ai-note-banner"
      role="status"
      data-attempt-result="true"
      data-pid={result.pid}
      data-verdict={result.verdict || ''}
      data-credited={String(result.credited)}>
      <strong data-result-note="true">{result.note}</strong>
      <div data-credit={credit.kind}>{credit.text}</div>
      <div className="ai-note-meta">
        判定 <code data-verdict-raw="true">{result.verdict}</code> · 来源{' '}
        <code>{result.source}</code>
        {result.confidence !== null ? (
          <>
            {' '}
            · 置信度 <code>{result.confidence}</code>
          </>
        ) : null}
        {result.provider || result.model ? (
          <>
            {' '}
            · <code>{result.provider}</code>/<code>{result.model}</code>
          </>
        ) : null}
        {result.run_id ? (
          <>
            {' '}
            · 调用留档 <code>{result.run_id}</code>
          </>
        ) : null}
      </div>
      <div className="ai-note-meta" data-queue-effect="true">
        这道题已经不在队列里了：判完立刻进冷却，也就从默认打印清单里消失。
      </div>
    </div>
  );
}
