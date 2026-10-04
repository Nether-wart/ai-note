/**
 * **故意做坏的组件**（#8）：自检的报警能力靠它们证明。
 *
 * 原型的验收记录把这件事说得很直白（`docs/acceptance-log.md:253`）：
 * 「一个从没失败过的检查，和一个从没通过过的检查，同样不可信」。
 * 所以自检每一趟都要把判据对着一份**真渲染出来的坏 HTML** 跑一次——
 * 判据不再响的那一天，`selftest.mjs` 自己会失败。
 *
 * 这三个组件是**真组件的那种坏法**，不是随手拼的字符串：
 *   · `ShowsOriginal`：把 `images.clean` 取不到时退回 `images.original`（裁决 D4 堵死的那条路）；
 *   · `SolutionWithAnswerBox`：解答题照样给作答框（题型与通道脱钩）；
 *   · `FallsBackToFirst`：越界时把 i 夹到 0——原型那两处静默过滤的等价物。
 */
import React from 'react';

/** 题面退回原图：原图印着订正，答案就在眼前。 */
export function ShowsOriginal({problem, apiBase}) {
  return (
    <article data-current-pid={problem.id} data-answer-mode="none">
      <img src={`${apiBase}${problem.images.original}`} alt="原图" data-image-kind="clean" />
    </article>
  );
}

/** 解答题给了过程输入框：过程题在屏幕上敲不出过程，这个框没有意义。 */
export function SolutionWithAnswerBox({problem}) {
  return (
    <article data-current-pid={problem.id} data-answer-mode="fillin">
      <form data-answer-form="fillin">
        <label data-answer-input="fillin">
          过程：
          <textarea name="answer" />
        </label>
      </form>
    </article>
  );
}

/** 把正解文本直接印在页面上（判定之前就把答案给人看）。 */
export function LeaksAnswer({problem}) {
  return (
    <article data-current-pid={problem.id} data-answer-mode="none">
      <p>{problem.correct_solution}</p>
    </article>
  );
}

/** 越界悄悄落到第一题（原型 `proto/server.py:320-321`／`:933-936` 的等价物）。 */
export function FallsBackToFirst({queue, apiBase}) {
  const pid = queue[0];
  return (
    <div data-redo-page="true">
      <article data-current-pid={pid} data-answer-mode="choice">
        <img src={`${apiBase}/api/problem/${pid}/image/clean`} alt="题面" data-image-kind="clean" />
      </article>
    </div>
  );
}
