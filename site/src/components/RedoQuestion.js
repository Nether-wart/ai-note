import React, {useEffect, useState} from 'react';
import {assetUrl} from '../lib/api';
import {answerMode, problemImage} from '../lib/redo';

/**
 * 重做页的题面与作答区。
 *
 * 三条界面不变量（spec #1，`#8` 会对着它们写检查）：
 *   1. **只渲染擦除手写后的题面**（`images.clean`）；缺图就明说，**绝不回退到原图**
 *      ——原图印着订正，把答案摆在做题的人面前，重做就没有意义了（裁决 D4）。
 *   2. **输入随题型分化**：选择给选项、填空给一个空、**解答题不给作答框**
 *      （过程题在屏幕上敲不出过程）。给不给框的判据来自服务端的 `auto_judge` 读数，
 *      界面不自己再判一遍。
 *   3. **不渲染标准答案、正解、原答、订正**：提交之前页面上根本没有判定节点。
 */
export default function RedoQuestion({problem, apiBase, onSubmit, submitting}) {
  const mode = answerMode(problem);
  const image = problemImage(problem);
  const cleanUrl = assetUrl(apiBase, image.url);
  const [answer, setAnswer] = useState('');

  // 换了一道题就把上一次的作答清掉（队列推进时这一条必须生效）。
  useEffect(() => {
    setAnswer('');
  }, [problem.id]);

  const blockedText =
    problem.screen_redo?.blocker_text?.length > 0
      ? problem.screen_redo.blocker_text.join('；')
      : mode.reason_text || '这道题不能自动判定，只能纸上重做。';

  const canSubmit = mode.mode !== 'none' && answer.trim() !== '' && !submitting;

  function handleSubmit(event) {
    event.preventDefault();
    if (canSubmit) {
      onSubmit(answer);
    }
  }

  return (
    <article className="ai-note-card" data-current-pid={problem.id} data-answer-mode={mode.mode}>
      <div className="ai-note-badges">
        <span className="ai-note-badge">{problem.type_cn || problem.type}</span>
      </div>

      {/* 验收 2：题面 = images.clean。没有就喊，不回退、不静默 */}
      {cleanUrl ? (
        <img
          src={cleanUrl}
          alt={`${problem.id} 擦除手写后的题面`}
          data-image-kind="clean"
        />
      ) : (
        <p className="ai-note-warn" data-missing-clean="true" role="alert">
          {image.message}（这是索引里的读数，不是没渲染完）
        </p>
      )}

      <div data-transcript="true">{problem.transcript}</div>

      {mode.mode === 'none' ? (
        <div className="ai-note-banner" role="alert" data-answer-blocked={mode.reason}>
          <strong>这道题不给作答框。</strong>
          <p>{blockedText}</p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} data-answer-form={mode.mode}>
          {mode.mode === 'choice' && mode.options.length > 0 && (
            <fieldset data-answer-input="choice">
              <legend>选一个选项</legend>
              {mode.options.map((option) => (
                <label key={option.label} className="ai-note-option">
                  <input
                    type="radio"
                    name="answer"
                    value={option.label}
                    checked={answer === option.label}
                    onChange={() => setAnswer(option.label)}
                  />{' '}
                  {option.label}. {option.text}
                </label>
              ))}
            </fieldset>
          )}

          {/* 选项缺失的选择题：**单输入框的填空回退**（最终修复 pass 作业单 1）。
              修前这里只有一句「只能自己填写作答」，却没有输入框——文案与行为矛盾，
              提交永远 disabled。标准答案还在，敲字母是合法作答，所以给框、并如实说明。 */}
          {mode.fallback === 'options_missing' && (
            <p className="ai-note-warn" data-options-empty="true">
              {mode.notice}
            </p>
          )}

          {mode.mode === 'fillin' && (
            <label data-answer-input="fillin">
              {mode.fallback === 'options_missing' ? '作答：' : '填空：'}
              <input
                type="text"
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                autoComplete="off"
              />
            </label>
          )}

          <button type="submit" disabled={!canSubmit} data-submit-attempt="true">
            {submitting ? '正在问判定…' : '提交作答（判定由服务做）'}
          </button>
        </form>
      )}

      {/* 警告按服务给的 level 区分呈现：hint 不是错误（契约 §2，级别只有服务能定） */}
      {(problem.warnings || []).map((warning) => (
        <p
          key={`${warning.code}-${warning.id || ''}`}
          className={warning.level === 'hint' ? 'ai-note-hint' : 'ai-note-warn'}
          data-warning-code={warning.code}
          data-level={warning.level || 'warning'}>
          {warning.level === 'hint' ? '提示：' : '⚠ '}
          {warning.message}
        </p>
      ))}
    </article>
  );
}
