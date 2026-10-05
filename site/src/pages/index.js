import React from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import {subjectUrl} from '../lib/routes';
import {overview} from '../lib/tree';
import {useIndex, useWorkbench} from '../components/Shell';

/**
 * 首页总览（跨科目）。
 *
 * 一行一个科目，读数是**服务给的**（`overview()` 只是把它们摆在一起，不另算一个指标）：
 *
 *   在池（未毕业）／冷却／毕业 · 未审核 · 今日份（默认打印清单里的道数）
 *
 * 三件事按纪律落在这里：
 *   1. **未归类**单独一行，道数照服务给的写——它不被任何一科悄悄吸收；
 *   2. **这一页不印答案**：它不是审核页（`CONTEXT.md`：答案只在阅读页出现）；
 *   3. **词表有问题要显眼**（`overview().vocabularyWarning`）：词表是「有哪些科目」的
 *      唯一来源，它缺席或为空时侧栏会空——那必须是一件看得见的事，不许变成一个安静的
 *      空侧栏（服务的话照原话显示，界面不重拼）。
 */
export default function Home() {
  const index = useIndex();
  const workbench = useWorkbench();
  const data = index?.phase === 'ready' ? index.data : null;
  const model = data ? overview(data) : null;

  return (
    <Layout title="首页总览" description="跨科目总览：每科目一行读数（在池／冷却／毕业、未审核、今日份）">
      <main className="container margin-vert--lg" data-page="overview">
        <h1>错题总览</h1>

        <div className="ai-note-workbench__toolbar">
          <button
            type="button"
            className="button button--primary"
            onClick={() => workbench?.open(null)}
            disabled={!workbench}
            data-action="open-workbench"
            data-from="overview">
            上传整页照片
          </button>
          <span className="ai-note-meta">
            照片 → 切分 → 人工调整 → 入库。
          </span>
        </div>

        {!model ? (
          <NotReady index={index} />
        ) : (
          <>
            {/* 词表缺席／为空：显眼处照原话显示（ADR 0007：不许静默） */}
            {model.vocabularyWarning && (
              <div
                className="ai-note-banner"
                role="alert"
                data-vocabulary-warning={model.vocabularyWarning.code}>
                <strong>受控词表有问题（科目那一级会画不出来）</strong>
                <p data-error-message="true">{model.vocabularyWarning.message}</p>
                <p className="ai-note-meta">
                  错误码：<code>{model.vocabularyWarning.code}</code>
                </p>
              </div>
            )}

            <p className="ai-note-summary" data-today="true">
              今天该做什么：默认打印清单里有{' '}
              <strong data-today-ready={model.totals.inDefaultList}>
                {model.totals.inDefaultList}
              </strong>{' '}
              道（未毕业且已脱离冷却）。
            </p>

            <table className="ai-note-table" data-overview-table="true">
              <thead>
                <tr>
                  <th>科目</th>
                  <th>在池（未毕业）</th>
                  <th>冷却</th>
                  <th>毕业</th>
                  <th>未审核</th>
                  <th>今日份</th>
                </tr>
              </thead>
              <tbody>
                {model.rows.map((row) => (
                  <tr key={row.name} data-overview-row={row.name}
                      data-off-vocabulary={String(row.offVocabulary)}>
                    <td>
                      <Link to={subjectUrl(row.name, 'detail')}>{row.label}</Link>
                      {row.offVocabulary && (
                        <span className="ai-note-warn" data-off-vocabulary="true">
                          {' '}（卡上的值不在受控词表里）
                        </span>
                      )}
                    </td>
                    {row.countsMissing ? (
                      <td colSpan={5} className="ai-note-warn" data-counts-missing="true">
                        服务没给这个科目的读数（不是 0 道）
                      </td>
                    ) : (
                      <>
                        {/*
                          在池 = 全部 − 毕业。两个数都是服务给的，界面只做这一条减法
                          （「毕业」不等于删除，所以它仍在「全部」里）。
                        */}
                        <td data-in-pool={row.counts.problems - row.counts.graduated}>
                          {row.counts.problems - row.counts.graduated}
                        </td>
                        <td data-cooling={row.counts.cooling}>{row.counts.cooling}</td>
                        <td data-graduated={row.counts.graduated}>{row.counts.graduated}</td>
                        <td data-unreviewed={row.counts.unreviewed}>{row.counts.unreviewed}</td>
                        <td data-today={row.today.ready}>{row.today.ready}</td>
                      </>
                    )}
                  </tr>
                ))}

                {/* 未归类：一等状态，单独一行，绝不并进任何一科 */}
                <tr data-overview-row="__unclassified__" data-unclassified="true">
                  <td>
                    <Link to={subjectUrl(null, 'detail')}>{model.unclassified.label}</Link>
                  </td>
                  <td colSpan={5} data-unclassified-count={model.unclassified.count}>
                    {model.unclassified.count} 道
                  </td>
                </tr>

                <tr data-overview-row="__totals__" data-totals="true">
                  <td>合计</td>
                  <td data-in-pool={model.totals.problems - model.totals.graduated}>
                    {model.totals.problems - model.totals.graduated}
                  </td>
                  <td data-cooling={model.totals.cooling}>{model.totals.cooling}</td>
                  <td data-graduated={model.totals.graduated}>{model.totals.graduated}</td>
                  <td className="ai-note-meta" data-unreviewed="missing">
                    服务没给
                  </td>
                  <td data-today={model.totals.inDefaultList}>{model.totals.inDefaultList}</td>
                </tr>
              </tbody>
            </table>
          </>
        )}
      </main>
    </Layout>
  );
}

/** 索引还没好：说清是在读、还是读不到（读不到的原话与重试在外壳的横幅里）。 */
function NotReady({index}) {
  if (!index || index.phase === 'loading') {
    return <p data-status="loading">正在从服务读索引…</p>;
  }
  return (
    <p className="ai-note-warn" data-status="failed">
      索引读不到，总览画不出来——失败的原话与重试按钮在上面那条横幅里。
    </p>
  );
}
