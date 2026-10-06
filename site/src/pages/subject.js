import React, {useCallback, useEffect, useMemo, useState} from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import {useLocation} from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import {fetchBrief, postBrief} from '../lib/api';
import {buildRedoUrl, queueForProblems, redoHeader} from '../lib/redo';
import {UNCLASSIFIED, parseSubjectQuery, problemUrl, subjectUrl} from '../lib/routes';
import {filterByTime, formatMoment, timeIndex} from '../lib/time';
import {briefState, detailOrder, outlineRows, problemsOfSubject} from '../lib/tree';
import {useMounted} from '../lib/use-mounted';
import FailurePanel from '../components/FailurePanel';
import RedoHeader from '../components/RedoHeader';
import {useIndex, useWorkbench} from '../components/Shell';

const VIEW_LABELS = {brief: '简报', detail: '细则', outline: '考点大纲', redo: '今日重做'};

/** 细则里题面转录只印**前若干字**：这一栏是索引，不是阅读页（全文在阅读页里）。 */
const TRANSCRIPT_PREFIX = 60;

/**
 * 科目下的一栏：`/subject?name=<科目>&view=<栏>`。
 *
 * 四条纪律：
 *   1. **认不出的栏位明确失败**（`parseSubjectQuery` 的 `view_unknown`），
 *      `data-error-code` 摆出来——**绝不**落到第一栏或第一个科目；
 *   2. 一栏一个组件，四栏共用首页那一份索引（一个页面只取一次索引）；
 *   3. **不印答案**：简报／细则／今日重做都不是审核页（答案只在阅读页出现）；
 *   4. 没有 name = **未归类**那一栏（一等状态）：简报与考点大纲都属于科目，
 *      所以那两栏对未归类**明说**为什么没有，而不是画一个空白。
 */
export default function Subject() {
  const location = useLocation();
  // 查询串**挂载之后**才读：构建期渲的是 `/subject` 这一条地址（没有 `?view=`），
  // 首次渲染必须与它一致，否则 React 报 hydrate 不匹配（见 `lib/use-mounted.js`）。
  const mounted = useMounted();
  const parsed = useMemo(
    () => (mounted ? parseSubjectQuery(location?.search || '') : null),
    [mounted, location?.search],
  );
  const index = useIndex();
  const workbench = useWorkbench();
  const {siteConfig} = useDocusaurusContext();
  const apiBase = siteConfig.customFields.apiBase;

  const data = index?.phase === 'ready' ? index.data : null;
  const name = parsed && !parsed.error ? parsed.name : null;
  const label = name || UNCLASSIFIED;
  const view = parsed && !parsed.error ? parsed.view : null;
  const viewLabel = view ? VIEW_LABELS[view] || view : '';

  return (
    <Layout title={`${label} · ${viewLabel}`} description="科目下的一栏：简报｜细则｜考点大纲｜今日重做">
      <main
        className="container margin-vert--lg"
        data-page="subject"
        data-subject={name || ''}
        data-view={view || ''}>
        <h1>
          {label}
          <small className="ai-note-meta"> · {viewLabel}</small>
        </h1>

        {!mounted ? (
          <p data-status="loading">正在打开这一栏…</p>
        ) : parsed.error ? (
          <FailurePanel title="这一栏打不开（地址里有认不出的东西）" error={parsed.error} />
        ) : !data ? (
          <NotReady index={index} />
        ) : view === 'brief' ? (
          <BriefView apiBase={apiBase} name={name} data={data} />
        ) : view === 'detail' ? (
          <DetailView
            data={data}
            name={name}
            month={parsed.month}
            week={parsed.week}
            onUpload={() => workbench?.open(null)}
          />
        ) : view === 'outline' ? (
          <OutlineView data={data} name={name} />
        ) : (
          <RedoView data={data} name={name} />
        )}
      </main>
    </Layout>
  );
}

function NotReady({index}) {
  if (!index || index.phase === 'loading') {
    return <p data-status="loading">正在从服务读索引…</p>;
  }
  return (
    <p className="ai-note-warn" data-status="failed">
      索引读不到，这一栏画不出来——失败的原话与重试按钮在外壳的横幅里。
    </p>
  );
}

// ------------------------------------------------------------------ 简报

/**
 * 简报：模型写的那段文字 + 它**用到的每一个数字**（`window_facts` / `history_facts`
 * 照抄，界面不重算）。三件事分开说：
 *
 *   · **没有简报**（404 `brief_missing`）是一个明确的**空态**（＋「生成」按钮），不是错误；
 *   · **生成失败**把服务的原话显示出来——尤其 502 `brief_unverifiable`：那是「模型编了
 *     数字」，这份简报**不落盘**，与「模型没问成」是两件事；
 *   · **简报过期**（`brief.stale`）照索引里的读数说「有 N 道新题没进去」。
 */
function BriefView({apiBase, name, data}) {
  const [state, setState] = useState({phase: 'loading', envelope: null, error: null});
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState(null);

  const load = useCallback(async () => {
    if (!name) {
      // 未归类不计入任何科目的简报（契约 §10.3）：这不是「读不到」，是它本来就没有
      setState({phase: 'unclassified', envelope: null, error: null});
      return;
    }
    setState({phase: 'loading', envelope: null, error: null});
    try {
      const envelope = await fetchBrief(apiBase, name);
      setState({phase: 'ready', envelope, error: null});
    } catch (error) {
      // **只有服务明说 `brief_missing` 才算空态**（契约 §9：404 下的 reason 取值）。
      // 认不出的 404（例如端点还没实现：`reason == "not_found"`、message 是「没有这条路由」）
      // 一律走失败面板照原话显示——拿 404 当「还没有简报」会把一句真话吞掉。
      const missing = error?.reason === 'brief_missing' || error?.code === 'brief_missing';
      setState({phase: missing ? 'missing' : 'failed', envelope: null, error});
    }
  }, [apiBase, name]);

  useEffect(() => {
    load();
  }, [load]);

  const generate = useCallback(async () => {
    setGenerating(true);
    setGenerateError(null);
    try {
      const envelope = await postBrief(apiBase, name);
      setState({phase: 'ready', envelope, error: null});
    } catch (error) {
      setGenerateError(error);
    } finally {
      setGenerating(false);
    }
  }, [apiBase, name]);

  const readout = briefState(data, name);

  if (state.phase === 'unclassified') {
    return (
      <section data-brief="unclassified">
        <p className="ai-note-warn">
          未归类没有简报：简报按科目生成，未归类的 {data.stats?.unclassified ?? 0} 道不计入。
        </p>
      </section>
    );
  }

  return (
    <section data-brief={state.phase}>
      {readout?.stale && (
        <p className="ai-note-warn" data-brief-stale="true">
          简报已过期：有 {readout.new_problems} 道新题没进去。
        </p>
      )}

      {state.phase === 'loading' && <p data-status="loading">正在从服务读简报…</p>}

      {state.phase === 'failed' && (
        <FailurePanel title="读不到简报" error={state.error} onRetry={load} />
      )}

      {state.phase === 'missing' && (
        <div className="ai-note-banner" data-brief-missing="true">
          <strong>这个科目还没有简报</strong>
          <p>简报要按需生成。</p>
          {state.error?.hint && <p className="ai-note-meta">服务的提示：{state.error.hint}</p>}
          {state.error?.details?.available && (
            <p className="ai-note-meta">
              历史上实际有哪几天：<code>{JSON.stringify(state.error.details.available)}</code>
            </p>
          )}
          <button type="button" className="button button--primary" onClick={generate} disabled={generating}
            data-action="generate-brief">
            {generating ? '正在问模型写一份…' : '生成这个科目的简报'}
          </button>
          {generateError && (
            <FailurePanel
              title="这次生成没成"
              error={generateError}
              extra={
                generateError?.reason === 'brief_unverifiable' || generateError?.code === 'brief_unverifiable'
                  ? '数字闸门没过：这份简报里至少有一个数字在索引里对不上，所以它没有落盘。'
                  : null
              }
            />
          )}
        </div>
      )}

      {state.phase === 'ready' && !state.envelope?.data?.brief && (
        // 服务说 200 却没给 brief：**不许静默**——明说这份回执里缺了什么
        <FailurePanel
          title="服务没有给出简报的内容"
          error={{
            code: 'brief_missing_in_envelope',
            message: '这一次读取的回执里没有 data.brief（HTTP 是成功的，内容却不在）。',
            hint: '照服务的原话查：可能是端点形状变了。',
            details: {keys: Object.keys(state.envelope?.data || {})},
          }}
        />
      )}

      {state.phase === 'ready' && state.envelope?.data?.brief && (
        <BriefBody
          brief={state.envelope.data.brief}
          path={state.envelope.data.path}
          generating={generating}
          generateError={generateError}
          onGenerate={generate}
        />
      )}
    </section>
  );
}

function BriefBody({brief, path, generating, generateError, onGenerate}) {
  // 时刻印给人读的那一个形状，原始串挂在 `title` 上（与「细则」的行同一条口径）：
  // 这一行并排四个时刻，全是 ISO 串时读起来只是一团数字。
  const generated = formatMoment(brief.generated_at);
  const windowFrom = formatMoment(brief.window_from);
  const windowUntil = formatMoment(brief.window_until);
  const covers = formatMoment(brief.covers_until);
  return (
    <article data-brief-body="true">
      <p className="ai-note-meta">
        生成于 <code title={generated.exact || undefined}>{generated.text}</code> · 窗口最近{' '}
        {brief.window_days} 天（
        <span title={windowFrom.exact || undefined}>{windowFrom.text}</span> →{' '}
        <span title={windowUntil.exact || undefined}>{windowUntil.text}</span>）· 数字依据的那份索引快照{' '}
        <code title={covers.exact || undefined}>{covers.text}</code> · 谁写的{' '}
        <code>{brief.provider}</code>/<code>{brief.model}</code>
        {path ? <> · 落盘在 <code>{path}</code></> : null}
      </p>

      <div data-brief-text="true">{brief.text}</div>

      {/*
        简报用到的每一个数字都摆出来（`path` 指向本次索引）。界面**不重算**——
        这些数字的闸门在服务那一侧（对不上就不落盘）。
      */}
      <h2>最近 {brief.window_days} 天用到的数字</h2>
      <FactList facts={brief.window_facts} basis="window" />
      <h2>全部历史用到的数字</h2>
      <FactList facts={brief.history_facts} basis="history" />

      <button type="button" className="button" onClick={onGenerate} disabled={generating}
        data-action="generate-brief">
        {generating ? '正在问模型重写一份…' : '重新生成（会问一次模型）'}
      </button>
      {generateError && (
        <FailurePanel
          title="这次生成没成"
          error={generateError}
          extra={
            generateError?.reason === 'brief_unverifiable' || generateError?.code === 'brief_unverifiable'
              ? '数字闸门没过：这份新简报里至少有一个数字在索引里对不上，所以它没有落盘，现在这一份还是旧的。'
              : null
          }
        />
      )}
    </article>
  );
}

function FactList({facts, basis}) {
  const list = facts || [];
  if (list.length === 0) {
    return (
      <p className="ai-note-meta" data-facts={basis} data-facts-count="0">
        这一半没有用到的数字（服务给的是空列表）。
      </p>
    );
  }
  return (
    <ul data-facts={basis} data-facts-count={list.length}>
      {list.map((fact, index) => (
        <li key={`${basis}-${index}-${fact.path}`} data-fact-path={fact.path} data-fact-value={fact.value}>
          {fact.label}：<strong>{String(fact.value)}</strong>
          <span className="ai-note-meta">
            {' '}
            （索引里的位置 <code>{fact.path}</code>）
          </span>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ 细则

/**
 * 细则：这个科目的**全部**错题，按**录入时间由新到老**（`detailOrder`）。
 *
 * 它不是默认打印清单的排法——那个按上次重做时间排，用它会让这份明细每重做一次就重排一次。
 * 每行只印题面转录的**前若干字**（不是全文）、题型、掌握读数、考点、错因，整行链到阅读页。
 * **不印正解／标准答案／原答／订正**：这一栏不是审核页。
 *
 * 顶上那片时间索引（`timeIndex`）是**入口**，筛不筛都印着：它是导航，不是结果。筛选状态
 * 住在地址里（`?month=`／`?week=`，`filterByTime`），所以它全是 `<Link>`——关掉 JS、
 * 刷新、把地址发给别人，看到的都是同一页。
 */
function DetailView({data, name, month, week, onUpload}) {
  const {ordered, undated} = useMemo(
    () => detailOrder(problemsOfSubject(data.problems, name)),
    [data.problems, name],
  );
  // 索引吃这一栏**全部**的题（含排不了时间的）：`timeIndex` 自己会把 undated 分出去，
  // 界面不另立一份「哪道题归哪个月」的规则（两份规则迟早会互相矛盾）。
  const buckets = useMemo(() => timeIndex(ordered), [ordered]);
  const filtered = useMemo(() => filterByTime(ordered, {month, week}), [ordered, month, week]);
  const filtering = Boolean(month || week);
  // 地址里那一段在索引里找不到 = **明确失败**。这时既不能退回「全部」（一次失效的链接
  // 会装成一次正常跳转），也不该只画一片空白：失败面板把键、服务的原话与出路一起说清。
  const broken = Boolean(filtered.error);
  const rows = broken ? [] : filtered.problems;

  return (
    <section data-detail="true" data-month={month || ''} data-week={week || ''}>
      <div className="ai-note-workbench__toolbar">
        <button type="button" className="button button--primary" onClick={onUpload} disabled={!onUpload}
          data-action="open-workbench" data-from="detail">
          上传整页照片
        </button>
        <span className="ai-note-meta">
          细则按录入时间由新到老排（{ordered.length} 道）；要印题面去「今日重做」。
        </span>
      </div>

      {/* 原生文档页的排法是「正文在左、辅助栏在右」——`DocItem/Layout` 里那个
          `<div className="col col--3">` 就是右侧栏的位置。时间索引站的就是那个位置，
          只是它要 `col--6` 才摆得下月份与周。DOM 顺序仍是**正文在前**：窄屏堆叠时
          先看到题表，右栏落到下面。 */}
      {/* 两列**照抄原生文档页的排法**（`@theme/DocItem/Layout`）：
          正文是 `col`（可长）＋ 一个封顶（原生用 `styles.docItemCol { max-width: 75% }`，
          这里按本项目的阅读宽封在 52rem），右栏是 `col col--3`。
          上一版把两列都写成固定宽度（`col--6` / `col--6`），等于没照抄，右栏被挤成一条缝。 */}
      <div className="row">
        <div className="col ai-note-detail__main">
      <div className="ai-note-detail__list">
        {broken ? (
          <FailurePanel title="地址里指的那一段，时间索引里没有" error={filtered.error} />
        ) : (
          <>
            {filtering && (
              <p data-detail-filter={filtered.key}>
                筛选：{filtered.label}（{rows.length} 道）{' '}
                <Link to={subjectUrl(name, 'detail')} data-filter-clear="true">
                  显示全部
                </Link>
              </p>
            )}

            {/* 「没有录入时间」那一行说的是**整份细则**；筛着的时候它会把不在筛选里的题
                也说成「列在最后」，读起来像筛选漏了人。所以只在没筛时出现。 */}
            {undated.length > 0 && !filtering && (
              <p className="ai-note-warn" data-undated={undated.length}>
                另有 {undated.length} 道没有录入时间，列在最后：
                {undated.map((problem) => (
                  <span key={problem.id}>
                    {' '}
                    <Link to={problemUrl(problem.id)}>{problem.id}</Link>
                  </span>
                ))}
              </p>
            )}

            {rows.length === 0 ? (
              filtering ? (
                // 桶就是从这份名单里数出来的，正常筛不出 0 道——但空清单会把「这一段里一道
                // 都没有」印成一片空白，而空白最容易被读成「一切正常」。
                <p data-detail-filter-empty="true">筛选：{filtered.label} 里一道题都没有。</p>
              ) : (
                <p data-detail-empty="true">
                  {name ? `「${name}」下还没有错题。` : '未归类下还没有错题。'}录入入口是上面那个「上传整页照片」。
                </p>
              )
            ) : (
              <ul className="ai-note-detail-list">
                {rows.map((problem) => {
                  // 行上只印给人读的时刻，原始串留在 `title` 里（`lib/time.js` 的口径）：
                  // `created_at` 是数据，不是用来读的。
                  const moment = formatMoment(problem.created_at);
                  return (
                    <li key={problem.id} data-problem-id={problem.id}>
                      <Link className="ai-note-detail-row" to={problemUrl(problem.id)}
                        data-detail-link="true">
                        <span data-transcript-prefix="true">
                          {transcriptPrefix(problem.transcript)}
                        </span>
                        <span className="ai-note-badges">
                          <span className="ai-note-badge">{problem.type_cn || problem.type}</span>
                          <span className="ai-note-badge" data-mastery={problem.mastery_cn}>
                            {problem.mastery_cn}
                          </span>
                          <span className="ai-note-badge">
                            连续对 {problem.streak ?? 0}/2
                          </span>
                          <span className="ai-note-badge" data-review={problem.review}>
                            {problem.review === 'reviewed' ? '已审核' : '未审核'}
                          </span>
                          <span className="ai-note-badge" data-created-at={problem.created_at}
                            title={moment.exact || undefined}>
                            录入 {moment.text}
                          </span>
                        </span>
                        <span className="ai-note-meta" data-topics={problem.topics?.length || 0}>
                          考点：{problem.topics?.length ? problem.topics.join('、') : '（还没有考点）'}
                        </span>
                        <span className="ai-note-meta" data-error-causes={problem.error_causes?.length || 0}>
                          错因：{problem.error_causes?.length ? problem.error_causes.join('、') : '（还没有错因）'}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}
      </div>
        </div>

        <div className="col col--3">
          {buckets.months.length > 0 ? (
            <TimeIndex name={name} months={buckets.months} />
          ) : (
            <p className="ai-note-meta" data-time-index-empty="true">
              还没有带录入时间的错题，所以时间索引空着。
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * 时间索引：一条全是链接的走廊——按月一张卡（`row` / `col col--3` / `card`，都是 Infima
 * 自己的排法），最近那一个月再按周分。
 *
 * 这里没有 `onClick`：点一张卡就是跳到那个地址，筛选是**地址**的一部分（「细则」那条纪律）。
 * 最近一个月的周索引由 `timeIndex` 挂在 `months[0].weeks` 上，所以只问它有没有，不另外算。
 */
function TimeIndex({name, months}) {
  return (
    <div className="row" data-time-index="true">
      {months.map((bucket) => (
        <div className="col col--3" key={bucket.key}>
          <div className="card">
            <div className="card__body">
              <Link to={subjectUrl(name, 'detail', {month: bucket.key})} data-month-card={bucket.key}>
                {bucket.label} <span className="badge badge--secondary">{bucket.count} 道</span>
              </Link>
              {bucket.weeks?.length ? (
                <ul className="ai-note-meta" data-weeks="true">
                  {bucket.weeks.map((week) => (
                    <li key={week.key}>
                      <Link
                        to={subjectUrl(name, 'detail', {month: bucket.key, week: week.key})}
                        data-week-card={week.key}>
                        {week.label} · 第 {week.week_no} 周 · {week.count} 道
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function transcriptPrefix(transcript) {
  const text = String(transcript || '').replace(/\s+/g, ' ').trim();
  if (!text) return '（这一道还没有题面转录）';
  return text.length > TRANSCRIPT_PREFIX ? `${text.slice(0, TRANSCRIPT_PREFIX)}…` : text;
}

// ------------------------------------------------------------------ 考点大纲

/** 考点大纲：章 → 节 → 点（`outlineRows()`）。没有就说没有，**不是一个空白**。 */
function OutlineView({data, name}) {
  if (!name) {
    return (
      <section data-outline="unclassified">
        <p className="ai-note-warn">
          未归类没有考点大纲：大纲挂在科目下（科目 → 章 → 节 → 点），未归类还没指定科目。
        </p>
      </section>
    );
  }

  const rows = outlineRows(data.outline, name);

  if (rows.length === 0) {
    return (
      <section data-outline="empty">
        <p data-outline-empty="true">
          这个科目还没有大纲（索引里的 <code>outline</code> 没有「{name}」这一支）。
          大纲是人定的东西，不在这里猜一份出来。
        </p>
      </section>
    );
  }

  return (
    <section data-outline="present" data-chapters={rows.length}>
      <ol className="ai-note-outline">
        {rows.map((chapter) => (
          <li key={chapter.chapter} data-chapter={chapter.chapter}>
            <strong>{chapter.chapter}</strong>
            <ul>
              {chapter.sections.map((section) => (
                <li key={section.section} data-section={section.section}
                  data-points={section.points.length}>
                  {section.section}
                  {section.points.length === 0 ? (
                    <span className="ai-note-meta">（这一节还没有点）</span>
                  ) : (
                    <ul>
                      {section.points.map((point) => (
                        <li key={point} data-point={point}>
                          {point}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ------------------------------------------------------------------ 今日重做

/**
 * 今日重做：这个科目的默认打印清单 +「显示冷却中的题」开关。
 *
 * 推导规则**一个字都没改**，全部来自既有实现：队列 = `queueForProblems(...)`，
 * 链接 = `buildRedoUrl(...)`，开关只改「取哪一套服务算好的总体」
 * （`screen_redo.bases`，契约 §6.1）。队列进了 URL 就是**一次快照**——判完一道题的
 * 瞬间它进了冷却、从默认清单里消失，服务端现算的「第 i+1 题」会漂到别处去。
 */
function RedoView({data, name}) {
  const defaultBasis = data.screen_redo?.default_basis || 'in_default_list';
  const [includeCooling, setIncludeCooling] = useState(false);
  const basis = includeCooling ? 'including_cooling' : defaultBasis;

  const problems = problemsOfSubject(data.problems, name);
  const queue = queueForProblems(problems, basis);
  const header = redoHeader(data, basis);

  return (
    <section data-redo="true" data-basis={basis}>
      <p className="ai-note-meta">
        {name ? `「${name}」` : '未归类'}下共 {problems.length} 道；其中能屏幕重做的是{' '}
        <strong data-subject-ready={queue.length}>{queue.length}</strong> 道。
      </p>

      <label className="ai-note-field">
        <input
          type="checkbox"
          checked={includeCooling}
          onChange={(event) => setIncludeCooling(event.target.checked)}
          data-toggle="including-cooling"
        />{' '}
        显示冷却中的题（队列与下面两个数字一起换成「
        {data.screen_redo?.bases?.[basis]?.basis_text}」，都是服务算好的）
      </label>

      {/* N/M 两个数字照旧由服务的读数给（界面不重算、也不重拼那句话）。 */}
      {header.known ? (
        <>
          <RedoHeader header={header} />
          <p className="ai-note-meta" data-basis-scope="library">
            ⚠ 这两个数字数的是全库那一堆；{queue.length} 才是这个科目里的。
          </p>
        </>
      ) : (
        <FailurePanel
          title="候选总体认不出"
          error={{
            code: 'basis_unknown',
            message: `服务算好的几套候选总体里没有这个：'${header.basis}'`,
            details: {available_bases: header.available_bases},
          }}
        />
      )}

      {queue.length > 0 ? (
        <p>
          <Link
            className="button button--primary"
            to={buildRedoUrl({queue, i: 0, basis})}
            data-redo-link="true"
            data-ready={queue.length}>
            开始屏幕重做（{queue.length} 道）
          </Link>
        </p>
      ) : (
        <p data-redo-empty="true">
          这一堆里没有能屏幕重做的题（原因见上面那两个数字：不能自动判定／缺擦除图）。
        </p>
      )}

      <p className="ai-note-meta">
        题面一律是擦除手写后的图；这一栏不印正解与标准答案。
      </p>
    </section>
  );
}
