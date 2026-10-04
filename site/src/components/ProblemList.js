import React, {useState} from 'react';
import ProblemCard from './ProblemCard';
import {buildRedoUrl, queueForProblems} from '../lib/redo';

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
          <li
            key={`${item.code || 'x'}-${index}`}
            // 级别只有服务能定（契约 §2）：hint 是提示、不是错误，呈现要弱一档
            className={item.level === 'hint' ? 'ai-note-hint' : undefined}
            data-warning-code={item.code}
            data-level={item.level || 'warning'}>
            {item.message}
            {item.id ? <span className="ai-note-meta">（{item.id}）</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AutoJudgeSummary({screenRedo, basis}) {
  // 两套候选总体都由服务算好，界面按开关**取**，不重算（契约 §6.1）。
  const selected = screenRedo?.bases?.[basis];
  if (!selected) {
    return null;
  }
  const byReason = selected.not_auto_judgeable?.by_reason || [];
  const detail = byReason.map((entry) => `${entry.reason_text}：${entry.count} 道`).join('；');

  return (
    <div
      className="ai-note-summary"
      data-screen-redo-summary="true"
      data-basis={basis}>
      <div data-basis-text={selected.basis_text}>
        标题里的数字数的是「{selected.basis_text}」这一堆：共{' '}
        {selected.ready + selected.blocked_total} 道，其中{' '}
        <strong data-ready={selected.ready}>{selected.ready}</strong> 道能屏幕重做。
      </div>
      <div data-count="not-auto-judgeable" data-count-value={selected.not_auto_judgeable?.count ?? 0}>
        另有 <strong>{selected.not_auto_judgeable?.count ?? 0}</strong> 道不能自动判定
        {detail ? `（${detail}）` : ''}，只能纸上重做。
      </div>
      {/*
        M 那句话**照服务给的原话显示**，界面不再自己拼一遍——拼了就会出现两句
        措辞几乎一样的话（`no_clean_image.message` 本来就是这句话）。
      */}
      <div
        data-count="no-clean-image"
        data-count-value={selected.no_clean_image?.count ?? 0}
        data-ids={(selected.no_clean_image?.ids || []).join(',')}>
        {selected.no_clean_image?.message}
      </div>
    </div>
  );
}

/**
 * 「显示冷却中的题」开关 + 屏幕重做的入口（#7）。
 *
 * 开关只改**取哪一套服务算好的总体**（`bases.including_cooling`），队列与 N/M 一起换，
 * 一处都不重算（契约 §6.1 第 1 条）。做过这一批的题在服务端进了冷却、从默认清单里
 * 消失，所以这条链接是**一次快照**：重做页把队列原样放在 URL 里带着走。
 */
function RedoEntry({problems, screenRedo, basis, includeCooling, onToggleCooling}) {
  const queue = queueForProblems(problems, basis);
  const basisText = screenRedo?.bases?.[basis]?.basis_text;

  return (
    <div className="ai-note-summary" data-redo-entry="true" data-basis={basis}>
      <label>
        <input
          type="checkbox"
          checked={includeCooling}
          onChange={(event) => onToggleCooling(event.target.checked)}
          data-toggle="including-cooling"
        />{' '}
        显示冷却中的题（队列与上面两个数字一起换成「{basisText}」，都是服务算好的）
      </label>
      <div>
        {queue.length > 0 ? (
          <a
            className="button button--primary"
            href={buildRedoUrl({queue, i: 0, basis})}
            data-redo-link="true"
            data-ready={queue.length}>
            开始屏幕重做（{queue.length} 道）
          </a>
        ) : (
          <p data-redo-empty="true">
            这一堆里没有能屏幕重做的题（原因见上面那两个数字：不能自动判定／缺擦除图）。
          </p>
        )}
      </div>
    </div>
  );
}

export default function ProblemList({envelope, apiBase}) {
  const data = envelope.data;
  const problems = data.problems || [];
  const stats = data.stats || {};
  const [includeCooling, setIncludeCooling] = useState(false);

  // 开关不勾 → 服务给的 default_basis（默认打印清单）；勾上 → 未毕业含冷却中。
  // 名字取自服务，界面不写死（契约 §6.1）。
  const basis = includeCooling
    ? 'including_cooling'
    : data.screen_redo?.default_basis || 'in_default_list';

  return (
    <>
      <div className="ai-note-summary" data-stats="true">
        共 <strong>{data.count}</strong> 道题（默认清单里 {stats.in_default_list ?? 0} 道，
        冷却中 {stats.cooling ?? 0} 道，已毕业 {stats.graduated ?? 0} 道，
        可自动判定 {stats.auto_judge_eligible ?? 0} 道）；索引建于{' '}
        <code>{data.built_at}</code>。
        {data.server?.read_only ? ' 服务是只读的（v0）。' : ''}
      </div>

      <AutoJudgeSummary screenRedo={data.screen_redo} basis={basis} />

      <RedoEntry
        problems={problems}
        screenRedo={data.screen_redo}
        basis={basis}
        includeCooling={includeCooling}
        onToggleCooling={setIncludeCooling}
      />

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
