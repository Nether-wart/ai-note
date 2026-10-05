import React, {useCallback, useEffect, useMemo, useState} from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import {useLocation} from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {assetUrl, fetchProblem} from '../lib/api';
import {buildRedoUrl} from '../lib/redo';
import {parseProblemQuery} from '../lib/routes';
import {useMounted} from '../lib/use-mounted';
import FailurePanel from '../components/FailurePanel';

/**
 * 阅读页：`/problem?pid=<题卡 id>`。**答案只在这一页出现**（spec #17 §5）。
 *
 * 题面那条铁律（裁决 D4）：
 *   · 默认只渲染 `images.clean`（擦除手写后的题面）；
 *   · 切到 `images.original` 必须是**显式、带标记的动作**（下面那个开关），
 *     而且切过去之后页面上要说清「原图印着手写与订正」；
 *   · **缺擦除图就明说缺**（`data-missing-clean`），**绝不静默回退到原图**。
 *
 * 没有 pid 时不猜一道题：`parseProblemQuery` 的失败原样摆在 `data-error-code` 上。
 */
export default function Problem() {
  const location = useLocation();
  // 查询串**挂载之后**才读（构建期那份 HTML 是按没有 `?pid=` 的地址渲的）：
  // 理由与代价见 `lib/use-mounted.js`。
  const mounted = useMounted();
  const parsed = useMemo(
    () => (mounted ? parseProblemQuery(location?.search || '') : null),
    [mounted, location?.search],
  );
  const {siteConfig} = useDocusaurusContext();
  const apiBase = siteConfig.customFields.apiBase;
  const [state, setState] = useState({phase: 'loading', envelope: null, error: null});
  const [showOriginal, setShowOriginal] = useState(false);

  const load = useCallback(
    async (pid) => {
      setState({phase: 'loading', envelope: null, error: null});
      try {
        const envelope = await fetchProblem(apiBase, pid);
        setState({phase: 'ready', envelope, error: null});
      } catch (error) {
        setState({phase: 'failed', envelope: null, error});
      }
    },
    [apiBase],
  );

  useEffect(() => {
    if (!parsed || parsed.error) return;
    // 换了一道题就把「看原图」这个显式动作复位：它是**一次**动作，不是一个默认状态
    setShowOriginal(false);
    load(parsed.pid);
  }, [parsed, load]);

  return (
    <Layout
      title={!parsed || parsed.error ? '阅读页' : `题目 ${parsed.pid}`}
      description="题意、正解与标准答案、原解与订正、考点与错因、重做历史">
      <main
        className="container margin-vert--lg"
        data-page="problem"
        data-pid={parsed && !parsed.error ? parsed.pid : ''}>
        {!mounted ? (
          <p data-status="loading">正在打开这一页…</p>
        ) : parsed.error ? (
          <FailurePanel title="这一页打不开（地址里有认不出的东西）" error={parsed.error} />
        ) : state.phase === 'loading' ? (
          <p data-status="loading">正在从服务读这道题…</p>
        ) : state.phase === 'failed' ? (
          <FailurePanel title="读不到这道题" error={state.error} onRetry={() => load(parsed.pid)} />
        ) : state.envelope?.data ? (
          <ProblemBody
            problem={state.envelope.data}
            envelope={state.envelope}
            apiBase={apiBase}
            showOriginal={showOriginal}
            onToggleOriginal={setShowOriginal}
          />
        ) : (
          <FailurePanel
            title="服务没有给出这道题的内容"
            error={{
              code: 'problem_missing_in_envelope',
              message: '这一次读取的回执里没有 data（HTTP 是成功的，内容却不在）。',
              hint: '照服务的原话查：可能是端点形状变了。',
            }}
          />
        )}
      </main>
    </Layout>
  );
}

function ProblemBody({problem, envelope, apiBase, showOriginal, onToggleOriginal}) {
  const clean = problem.images?.clean || null;
  const original = problem.images?.original || null;
  const cleanUrl = assetUrl(apiBase, clean);
  const originalUrl = assetUrl(apiBase, original);
  const canRedo = problem.screen_redo?.ready === true;

  return (
    <article data-problem-body="true">
      <h1>
        {problem.type_cn || problem.type}
        <small className="ai-note-meta">
          {' '}
          · {problem.subject || '未归类'} · {problem.review === 'reviewed' ? '已审核' : '未审核'}
        </small>
      </h1>
      <p className="ai-note-meta">
        题卡 <code>{problem.id}</code> · 录入 {problem.created_at || '（服务没给时刻）'} · 重做过{' '}
        {problem.attempts ?? 0} 次 · 掌握读数 {problem.mastery_cn}
        {problem.cooling ? `（还在冷却，还差 ${problem.cooldown_days} 天）` : ''}
      </p>

      <figure className="ai-note-page-photo">
        {cleanUrl ? (
          <img src={cleanUrl} alt="题面（擦除手写后的图）" data-image-kind="clean" />
        ) : (
          <p className="ai-note-warn" data-missing-clean="true" role="alert">
            这道题没有擦除图：不回退到原图（原图印着手写与订正，那里面就有答案）。
          </p>
        )}

        {/*
          原图只能由**显式、带标记的动作**打开（裁决 D4）。默认关着；
          打开之后页面上要留一句「你现在看的是原图」。
        */}
        <p>
          <label className="ai-note-field" data-action="show-original">
            <input
              type="checkbox"
              checked={showOriginal}
              onChange={(event) => onToggleOriginal(event.target.checked)}
              data-toggle="show-original"
            />{' '}
            显示原图（含手写与订正，里面有答案）
          </label>
        </p>

        {showOriginal &&
          (originalUrl ? (
            <>
              <p className="ai-note-warn" data-explicit-original="true">
                你现在看的是原图：它印着原解与订正，只用于审核这一页。
              </p>
              <img src={originalUrl} alt="原题（未擦除，印着手写与订正）" data-image-kind="original" />
            </>
          ) : (
            <p className="ai-note-warn" data-original-missing="true">
              服务连原图都没给：没有可以看的照片。
            </p>
          ))}

        <figcaption className="ai-note-meta">
          题面转录（检索与打标用，不用于印刷）：
        </figcaption>
      </figure>
      <p data-transcript="true">{problem.transcript || '（还没有题面转录）'}</p>

      <section data-answer-section="true">
        <h2>正解与标准答案</h2>
        <div className="ai-note-card">
          <p>
            <strong>标准答案</strong>
            <span className="ai-note-meta">（自动判定的唯一基准）</span>：
          </p>
          <p data-field="standard_answer">{problem.standard_answer ?? '（还没有）'}</p>
          <p>
            <strong>正解</strong>
            <span className="ai-note-meta">（给人看的完整解答）</span>：
          </p>
          <p data-field="correct_solution">{problem.correct_solution || '（还没有正解）'}</p>
        </div>
      </section>

      <section data-original-section="true">
        <h2>原解与订正</h2>
        <div className="ai-note-card">
          <p>
            <strong>原答</strong>
            <span className="ai-note-meta">（你原本给出的最终答案；看不出来时留空，绝不猜）</span>：
          </p>
          <p data-field="original_answer">
            {problem.original_answer ?? '（看不出来，留空）'}
          </p>
          <p>
            <strong>原解</strong>
            <span className="ai-note-meta">（你做错那次写下的解法，永不参与判定）</span>：
          </p>
          <p data-field="original_transcript">
            {problem.original_transcript || '（没有原解转录）'}
          </p>
          <p>
            <strong>订正</strong>
            <span className="ai-note-meta">（原答之后补上的，独立于正解的一条信息）</span>：
          </p>
          <p data-field="correction">{problem.correction || '（没有订正）'}</p>
        </div>
      </section>

      <section data-topics-section="true">
        <h2>考点与错因</h2>
        <p data-field="topics">
          考点：{problem.topics?.length ? problem.topics.join('、') : '（还没有考点）'}
        </p>
        <p data-field="error_causes">
          错因：{problem.error_causes?.length ? problem.error_causes.join('、') : '（还没有错因）'}
        </p>
        {problem.new_tag_proposals?.length ? (
          <p className="ai-note-meta" data-field="new_tag_proposals">
            待批的新标签提案：{problem.new_tag_proposals.join('、')}
          </p>
        ) : null}
      </section>

      <section data-attempts-section="true">
        <h2>重做历史</h2>
        <p className="ai-note-meta">
          掌握读数：{problem.mastery_cn} · 连续判定为对 {problem.streak ?? 0}/2 · 最近一次判定{' '}
          {problem.last_verdict_cn || '（还没有）'}（下面是`attempt_log`，最近{' '}
          {(problem.attempt_log || []).length} 条）
        </p>
        {(problem.attempt_log || []).length === 0 ? (
          <p data-attempt-log="0">还没有重做过。</p>
        ) : (
          <ul data-attempt-log={problem.attempt_log.length}>
            {problem.attempt_log.map((attempt, index) => (
              <li key={`${attempt.at}-${index}`} data-attempt-at={attempt.at}
                data-verdict={attempt.verdict}>
                <strong>{attempt.verdict_cn || attempt.verdict}</strong> ·{' '}
                {attempt.channel_cn || attempt.channel} ·{' '}
                {attempt.source_cn || attempt.source} · {attempt.at}
                {attempt.error_causes?.length ? ` · 错因 ${attempt.error_causes.join('、')}` : ''}
                {attempt.note ? <span className="ai-note-meta"> —— {attempt.note}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section data-redo-entry="true">
        <h2>重做这道题</h2>
        {canRedo ? (
          <p>
            <Link
              className="button button--primary"
              to={buildRedoUrl({queue: [problem.id], i: 0})}
              data-redo-link="true">
              重做这道题（题面是擦除手写后的图）
            </Link>
          </p>
        ) : (
          <p className="ai-note-warn" data-screen-redo-blocked="true">
            这道题不能屏幕重做，只能纸上重做：
            {(problem.screen_redo?.blocker_text || []).join('；') ||
              problem.auto_judge?.reason_text ||
              '（服务没给理由）'}
          </p>
        )}
      </section>

      {/* 逐卡警告照原话显示（ADR 0007 第 6 条：hint 是提示、不是错误）。 */}
      {(problem.warnings || []).map((warning, index) => (
        <p
          key={`${warning.code}-${index}`}
          className={warning.level === 'hint' ? 'ai-note-hint' : 'ai-note-warn'}
          data-warning-code={warning.code}
          data-level={warning.level || 'warning'}>
          {warning.level === 'hint' ? '提示：' : '⚠ '}
          {warning.message}
        </p>
      ))}

      {(envelope.warnings || []).length > 0 && (
        <details className="ai-note-banner" data-problem-notices="true">
          <summary>这封信封还带了 {envelope.warnings.length} 条警告（点开看原话）</summary>
          <ul>
            {envelope.warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`} data-warning-code={warning.code}>
                {warning.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </article>
  );
}
