import React from 'react';
import ProblemCard from './ProblemCard';

/**
 * 清单页的主体：一封 `/api/index` 的信封进去，一组卡片出来。
 *
 * 「不许静默」在这里的落法：`warnings[]` 与 `skipped[]` 都**必须**显示出来，
 * 而且 §6.1 的两个数字（另有 N 道不能自动判定 / 另有 M 道缺擦除图）要说在明处
 * ——静默缩短的清单正是这个项目反复被咬的那类失败。
 */

function Notice({title, items}) {
  if (!items || items.length === 0) {
    return null;
  }
  return (
    <div className="ai-note-banner" role="status" data-notice={title}>
      <strong>{title}</strong>
      <ul>
        {items.map((item, index) => (
          <li key={`${item.code || 'x'}-${index}`}>
            {item.message}
            {item.id ? <span className="ai-note-meta">（{item.id}）</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AutoJudgeSummary({screenRedo}) {
  // 两套候选总体都由服务算好，界面按开关**取**，不重算（契约 §6.1）。
  const basis = screenRedo?.bases?.[screenRedo.default_basis];
  if (!basis) {
    return null;
  }
  const byReason = basis.not_auto_judgeable?.by_reason || [];
  const detail = byReason.map((entry) => `${entry.reason_text}：${entry.count} 道`).join('；');

  return (
    <div
      className="ai-note-summary"
      data-screen-redo-summary="true"
      data-basis={screenRedo.default_basis}>
      <div data-basis-text={basis.basis_text}>
        标题里的数字数的是「{basis.basis_text}」这一堆：共{' '}
        {basis.ready + basis.blocked_total} 道，其中 <strong>{basis.ready}</strong> 道能屏幕重做。
      </div>
      <div data-count="not-auto-judgeable" data-count-value={basis.not_auto_judgeable?.count ?? 0}>
        另有 <strong>{basis.not_auto_judgeable?.count ?? 0}</strong> 道不能自动判定
        {detail ? `（${detail}）` : ''}，只能纸上重做。
      </div>
      {/*
        M 那句话**照服务给的原话显示**，界面不再自己拼一遍——拼了就会出现两句
        措辞几乎一样的话（`no_clean_image.message` 本来就是这句话）。
      */}
      <div
        data-count="no-clean-image"
        data-count-value={basis.no_clean_image?.count ?? 0}
        data-ids={(basis.no_clean_image?.ids || []).join(',')}>
        {basis.no_clean_image?.message}
      </div>
    </div>
  );
}

export default function ProblemList({envelope, apiBase}) {
  const data = envelope.data;
  const problems = data.problems || [];
  const stats = data.stats || {};

  return (
    <>
      <div className="ai-note-summary" data-stats="true">
        共 <strong>{data.count}</strong> 道题（默认清单里 {stats.in_default_list ?? 0} 道，
        冷却中 {stats.cooling ?? 0} 道，已毕业 {stats.graduated ?? 0} 道，
        可自动判定 {stats.auto_judge_eligible ?? 0} 道）；索引建于{' '}
        <code>{data.built_at}</code>。
        {data.server?.read_only ? ' 服务是只读的（v0）。' : ''}
      </div>

      <AutoJudgeSummary screenRedo={data.screen_redo} />

      <Notice title="索引里的警告" items={envelope.warnings} />
      <Notice title="没能建出来的记录（这些题不在上面的清单里）" items={envelope.skipped} />

      {problems.length === 0 ? (
        <p data-empty="true">
          这个数据目录里还没有题卡。服务读的是 <code>{data.server?.public_base}</code> 背后的
          <code>data/problems/</code>。
        </p>
      ) : (
        <div className="ai-note-cards">
          {problems.map((problem) => (
            <ProblemCard key={problem.id} problem={problem} apiBase={apiBase} />
          ))}
        </div>
      )}
    </>
  );
}
