import React from 'react';

/**
 * 失败一律是一个**显式面板**：说清「读不到什么、怎么办、错误码是什么」，
 * 并给一个重试按钮（`site/README.md` 的界面纪律）。
 *
 * 两条不许：
 *   · **不许 `catch {}`** —— 面板上显示的一定是**服务自己说的话**（`message` / `hint`
 *     / 码），界面不重拼一句委婉说法（拼了就会出现两句几乎一样的话）；
 *   · **不许落向一个看起来正常的状态** —— 越界、读不到、认不出的栏位都从这里出去，
 *     带 `data-error-code`，好让判据对着它断言（`#8` 的口径）。
 */
export default function FailurePanel({title, error, onRetry = null, extra = null, retryLabel = '重试'}) {
  return (
    <div
      className="ai-note-banner"
      role="alert"
      data-status="failed"
      data-error-code={error?.code || error?.reason || 'unknown_error'}
      data-error-reason={error?.reason || ''}>
      <strong>{title}</strong>
      <p data-error-message="true">{error?.message}</p>
      {error?.hint && <p className="ai-note-meta">怎么办：{error.hint}</p>}
      <p className="ai-note-meta">
        错误码：<code>{error?.code}</code>
        {error?.reason && error.reason !== error.code ? (
          <>
            ／<code>{error.reason}</code>
          </>
        ) : null}
        {error?.status ? `（HTTP ${error.status}）` : ''}
      </p>
      {error?.details && Object.keys(error.details).length > 0 && (
        <p className="ai-note-meta">
          细节：<code>{JSON.stringify(error.details)}</code>
        </p>
      )}
      {extra && (
        <p className="ai-note-warn" data-extra-hint="true">
          {extra}
        </p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry} data-action="retry">
          {retryLabel}
        </button>
      )}
    </div>
  );
}
