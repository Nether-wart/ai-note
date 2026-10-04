import React from 'react';

/**
 * 重做页的页头（验收 5）：**另有 N 道题不能自动判定**，以及**另有 M 道缺擦除图**。
 *
 * 两条纪律：
 *   1. 数字与文案**只从服务给的 `screen_redo.bases` 里取**（裁决 D3/D4），界面不重算、
 *      也不重拼一句等价文案——`no_clean_image.message` 就是服务给的原话，照抄。
 *   2. N 没有现成句子，所以由界面排版，但**必须逐个列出理由码与道数**（裁决 D3）：
 *      只说「另有 N 道」等于把「少了哪几道、为什么」藏起来。
 */
export default function RedoHeader({header}) {
  const block = header?.not_auto_judgeable || {count: 0, by_reason: []};
  const byReason = block.by_reason || [];
  const noClean = header?.no_clean_image || {count: 0, ids: [], message: null};
  const total = (header?.ready ?? 0) + (header?.blocked_total ?? 0);

  return (
    <div className="ai-note-summary" data-redo-header="true" data-basis={header?.basis || ''}>
      <div data-basis-text={header?.basis_text || ''}>
        这一批数的是「{header?.basis_text}」这一堆：共 {total} 道，其中{' '}
        <strong data-ready={header?.ready ?? 0}>{header?.ready ?? 0}</strong> 道能屏幕重做。
      </div>

      <div data-count="not-auto-judgeable" data-count-value={block.count ?? 0}>
        另有 <strong>{block.count ?? 0}</strong> 道题不能自动判定（解答题／无标准答案／未审核），
        只能纸上重做。
      </div>
      {byReason.length > 0 && (
        <ul data-reasons="not-auto-judgeable">
          {byReason.map((entry) => (
            <li key={entry.reason} data-reason={entry.reason} data-reason-count={entry.count}>
              {entry.reason_text}：{entry.count} 道（<code>{entry.reason}</code>）
              {entry.ids?.length ? (
                <span className="ai-note-meta">（{entry.ids.join('、')}）</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {/*
        M 的那句话由服务给（契约 §6.1 第 5 条）。界面**照原话显示**：
        自己再拼一遍就会出现两句措辞几乎一样的话——里程碑二第一次真实渲染时撞上过。
      */}
      <div
        data-count="no-clean-image"
        data-count-value={noClean.count ?? 0}
        data-ids={(noClean.ids || []).join(',')}>
        {noClean.message}
      </div>
    </div>
  );
}
