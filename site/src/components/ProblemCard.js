import React from 'react';
import {assetUrl} from '../lib/api';

/**
 * 一张题卡。
 *
 * 两条界面纪律（都来自 spec #1，不是审美偏好）：
 *   1. **只显示擦除手写后的题面图**。原图印着订正，把答案摆在做题的人面前，
 *      重做就没有意义了。所以这里没有「clean 取不到就退回 original」这种兜底
 *      ——取不到就说取不到。
 *   2. **每题都标出通道**（屏幕可做 / 只能纸上）。「题型决定可机判」这条规则必须可见，
 *      否则清单里少一道题看起来就像 bug。
 *
 * 这里**不显示**标准答案、正解、原答与订正：清单页不是审核页，没有理由把答案印在屏幕上。
 */

function Badge({children, kind}) {
  return <span className={`ai-note-badge${kind ? ` ai-note-badge--${kind}` : ''}`}>{children}</span>;
}

function ChannelBadge({screenRedo}) {
  if (screenRedo.ready) {
    return <Badge kind="screen">屏幕可做</Badge>;
  }
  return (
    <Badge kind="paper">
      只能纸上
      {screenRedo.blocker_text.length > 0 ? `（${screenRedo.blocker_text[0]}）` : ''}
    </Badge>
  );
}

function MasteryLine({problem}) {
  const parts = [`掌握：${problem.mastery_cn}`, `连续正确 ${problem.streak}/2`];
  if (problem.graduated) {
    parts.push('已毕业（退出默认清单）');
  } else if (problem.cooling) {
    parts.push(`冷却中，还有 ${problem.cooldown_days} 天`);
  } else {
    parts.push('已脱离冷却');
  }
  parts.push(`重做 ${problem.attempts} 次`);
  if (problem.last_verdict) {
    parts.push(`上次判定：${problem.last_verdict_cn}`);
  }
  return <div className="ai-note-meta">{parts.join(' · ')}</div>;
}

export default function ProblemCard({problem, apiBase}) {
  const screenRedo = problem.screen_redo || {ready: false, blockers: [], blocker_text: []};
  const cleanUrl = assetUrl(apiBase, problem.images?.clean);

  return (
    <article className="ai-note-card" data-problem-id={problem.id} data-channel={screenRedo.ready ? 'screen' : 'paper'}>
      <div className="ai-note-badges">
        <Badge>{problem.type_cn || problem.type}</Badge>
        <ChannelBadge screenRedo={screenRedo} />
        {problem.review === 'unreviewed' && <Badge>未审核</Badge>}
        {problem.in_default_list && <Badge>在默认清单里</Badge>}
      </div>

      <h2>{problem.id}</h2>

      {cleanUrl ? (
        <img
          src={cleanUrl}
          alt={`${problem.id} 擦除手写后的题面`}
          data-image-kind="clean"
          loading="lazy"
        />
      ) : (
        <p className="ai-note-warn" data-missing-clean="true">
          没有擦除手写后的题面图，也不回退到原图（原图印着订正）——这道题不能屏幕重做。
        </p>
      )}

      <div>{problem.transcript}</div>
      {problem.options?.length > 0 && (
        <ul className="ai-note-meta">
          {problem.options.map((option) => (
            <li key={option.label}>
              {option.label}. {option.text}
            </li>
          ))}
        </ul>
      )}

      <MasteryLine problem={problem} />
      <div className="ai-note-meta">
        考点：{problem.topics.length ? problem.topics.join('、') : '（空）'} · 错因：
        {problem.error_causes.length ? problem.error_causes.join('、') : '（空）'}
      </div>

      {(problem.warnings || []).map((warning) => (
        <p
          className={warning.level === 'hint' ? 'ai-note-hint' : 'ai-note-warn'}
          key={`${problem.id}-${warning.code}`}
          data-warning-code={warning.code}
          data-level={warning.level || 'warning'}>
          {warning.level === 'hint' ? '提示：' : '⚠ '} {warning.message}
          <span className="ai-note-meta">（{warning.code}／{warning.level || 'warning'}）</span>
        </p>
      ))}
    </article>
  );
}
