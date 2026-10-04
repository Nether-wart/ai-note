/**
 * 屏幕重做页的**界面不变量**（#8）：把「渲染结果里有什么／没有什么」写成可跑的判据。
 *
 * 三条纪律：
 *
 *   1. **不靠浏览器、不联网**：判据吃的是一个 HTML 字符串。真实渲染由
 *      `selftest.mjs` 用 React 的 `renderToStaticMarkup` 做（服务端渲染，不开浏览器），
 *      这个模块只负责判断——所以它自己没有任何依赖，`node --test` 裸跑就能验。
 *   2. **判据对着真实 HTML 写，不写 CSS 选择器**：原型的第一版自检查 `.gates`，
 *      而页面里写的是 `class="gates"`，于是 20 条全误报（`docs/acceptance-log.md:253`、
 *      `proto/server.py:983`）。这里的判据认 `<img>`／`<link>` 的真实属性写法。
 *   3. **不信声明，只信 URL**：`data-image-kind="clean"` 是组件自己贴的标签，
 *      一个把原图接上、标签忘了改的组件照样会这么说。所以判据同时看**声明**与
 *      **真实 URL**，两条各报各的（`selftest.mjs` 的坏夹具就是照这个做的）。
 *
 * 每条判据返回**违规数组**（空数组 = 这条不变量成立）。每个违规都带机器可读的 `code`，
 * 方便测试与自检断言；`details` 说清「哪儿、看到了什么」。
 */

import {answerMode, parseRedoQuery, resolveQueueItem} from '../src/lib/redo.js';

/** 违规码表。测试与自检都对着这些码断言，别改字面量。 */
export const CODES = Object.freeze({
  questionImageNotClean: 'question_image_not_clean',
  questionImageMissing: 'question_image_missing',
  questionImageKindMismatch: 'question_image_kind_mismatch',
  solutionHasAnswerBox: 'solution_has_answer_box',
  blockedQuestionHasAnswerBox: 'blocked_question_has_answer_box',
  answerBoxMissing: 'answer_box_missing',
  choiceOptionsMissing: 'choice_options_missing',
  answerModeMismatch: 'answer_mode_mismatch',
});

const IMAGE_URL_ATTRIBUTES = {
  img: ['src', 'srcset'],
  source: ['src', 'srcset'],
  video: ['poster'],
};

function violate(code, message, details = {}) {
  return {code, message, details};
}

// --------------------------------------------------------------- HTML 解析

const TAG_RE = /<([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)\/?>/g;
const ATTR_RE = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

/** 把所有标签拆成 `{name, attrs}`。自己渲染出来的 HTML 是规整的，够用。 */
export function parseTags(html) {
  const tags = [];
  for (const match of String(html || '').matchAll(TAG_RE)) {
    const attrs = {};
    for (const attr of match[2].matchAll(ATTR_RE)) {
      const value = attr[2] ?? attr[3] ?? attr[4];
      attrs[attr[1].toLowerCase()] = value === undefined ? '' : value;
    }
    tags.push({name: match[1].toLowerCase(), attrs});
  }
  return tags;
}

/**
 * 页面上**所有**图片引用：`<img src>`、`<img srcset>`、`<link as=image href>`
 * （React 19 会给 `<img>` 额外吐一个 preload link，那也是一个引用）、`<source>`。
 *
 * 漏掉 preload 就等于漏掉一个把原图地址写进 HTML 的地方。
 */
export function imageRefs(html) {
  const refs = [];
  for (const tag of parseTags(html)) {
    const names = IMAGE_URL_ATTRIBUTES[tag.name] || [];
    for (const name of names) {
      if (tag.attrs[name]) {
        refs.push({tag: tag.name, attr: name, url: tag.attrs[name], kind: tag.attrs['data-image-kind'] ?? null});
      }
    }
    if (tag.name === 'link' && tag.attrs.as === 'image' && tag.attrs.href) {
      refs.push({tag: 'link', attr: 'href', url: tag.attrs.href, kind: tag.attrs['data-image-kind'] ?? null});
    }
  }
  return refs;
}

/** 契约 §7 的擦除图路径。题面**只能是**它。 */
export function cleanImagePath(pid) {
  return `/api/problem/${pid}/image/clean`;
}

// ------------------------------------------- 不变量 1：题面只能是擦除后的图

/**
 * 验收 1（最重要的一条，spec #1 第 3 条 + 裁决 D4）：
 *
 *   · 重做页上出现的**每一个**图片引用都必须是这张卡的**擦除手写后的题面**；
 *   · 题面被换成原图（含手写与订正）／整页照片／掩膜图 → **必须报警**；
 *   · 一张图都没有（图没渲染出来）也是坏——不能因为"没有图"就默认通过。
 *
 * 原图把答案摆在眼前，重做就没有意义了，所以这条不能只查「有没有图」。
 */
export function checkQuestionImage({html, pid, apiBase = ''}) {
  const violations = [];
  const expected = cleanImagePath(pid);
  const refs = imageRefs(html);
  const cleanRefs = refs.filter((ref) => ref.url.endsWith(expected));

  for (const ref of refs) {
    if (!ref.url.endsWith(expected)) {
      violations.push(
        violate(
          CODES.questionImageNotClean,
          `题面区出现了不是擦除图的图：<${ref.tag} ${ref.attr}="${ref.url}">。` +
            '未擦除的原图印着订正，把答案摆在做题的人面前，重做就没有意义了。',
          {found: ref.url, expected, tag: ref.tag, attr: ref.attr},
        ),
      );
    }
  }

  for (const ref of refs.filter((ref) => ref.tag === 'img')) {
    if (ref.kind !== 'clean') {
      violations.push(
        violate(
          CODES.questionImageKindMismatch,
          `<img> 的 data-image-kind 是 ${ref.kind === null ? '（没有这个属性）' : `'${ref.kind}'`}，` +
            '不是 clean。判据靠这个标记认题面图，标错了就等于没有标记。',
          {kind: ref.kind, expected_kind: 'clean', url: ref.url},
        ),
      );
    }
  }

  if (cleanRefs.length === 0) {
    violations.push(
      violate(
        CODES.questionImageMissing,
        `这道题的擦除手写后的题面图没渲染出来（HTML 里找不到 ${expected}）。` +
          '题面缺图是显式的失败，不是"没有就算了"。',
        {expected, image_refs: refs.map((ref) => ref.url)},
      ),
    );
  }

  // 兜底：这两类地址无论从哪个属性、哪个 JSON 块漏出来都算漏题。
  const apiBaseSuffix = String(apiBase || '').replace(/\/+$/, '');
  const forbidden = [
    `/api/problem/${pid}/image/original`,
    `/api/problem/${pid}/image/mask`,
    '/api/page/',
    'data/pages/',
  ];
  for (const fragment of forbidden) {
    // 已经被上面的图片引用检查抓到过的，不重复报一遍（同一个根因只出一条）
    const already = violations.some((v) => String(v.details.found || '').includes(fragment));
    if (String(html || '').includes(fragment) && !already) {
      violations.push(
        violate(CODES.questionImageNotClean, `重做页的 HTML 里出现了未擦除的素材地址：${fragment}`,
          {found: fragment, api_base: apiBaseSuffix}),
      );
    }
  }

  return violations;
}

// ------------------------------------------- 不变量 2：解答题不得给出作答框

const ANSWER_AFFORDANCES = ['input', 'textarea', 'select'];

/**
 * 验收 2（spec #1 第 31 条）：**过程题在屏幕上敲不出过程**，所以解答题不给作答框；
 * 未审核／无标准答案的题同理（服务端的 `auto_judge` 三个理由，唯一实现在 `autojudge.py`）。
 *
 * 判据**不自己再判一遍**能不能自动判定：它问 `redo.js` 的 `answerMode`——那是界面
 * 消费服务读数的唯一入口（契约 §6：「这套判断只有这一份实现」）。
 *
 * 两个方向都查：**不该有的框不许有**，**该有的框不许丢**。只查前一个方向的话，
 * 一个把作答区整个渲染丢掉的组件会安静地通过。
 */
export function checkAnswerBox({html, problem}) {
  const violations = [];
  const expected = answerMode(problem);
  const tags = parseTags(html);

  const article = tags.find((tag) => tag.name === 'article' && 'data-current-pid' in tag.attrs);
  const declared = article ? (article.attrs['data-answer-mode'] ?? null) : null;
  const form = tags.some((tag) => tag.name === 'form' && 'data-answer-form' in tag.attrs);
  const inputs = tags.filter((tag) => ANSWER_AFFORDANCES.includes(tag.name));
  const radios = inputs.filter((tag) => tag.name === 'input' && tag.attrs.type === 'radio');
  const hasBox = form || inputs.length > 0;

  if (expected.mode === 'none' && hasBox) {
    const isSolution = problem?.type === 'solution' || expected.reason === 'solution_type';
    violations.push(
      violate(
        isSolution ? CODES.solutionHasAnswerBox : CODES.blockedQuestionHasAnswerBox,
        isSolution
          ? '解答题给出了作答框：过程题在屏幕上敲不出过程，这道题只能人工确认（纸上重做）。'
          : `这道题不能自动判定（${expected.reason}），却给出了作答框。`,
        {
          reason: expected.reason,
          type: problem?.type ?? null,
          found: form ? 'form[data-answer-form]' : inputs[0].name,
          blocked_text: expected.reason_text,
        },
      ),
    );
  }

  if (expected.mode === 'choice') {
    if (!hasBox) {
      violations.push(violate(CODES.answerBoxMissing, '选择题的作答区没了：题面渲染出来了，选项却没渲染。',
        {expected: 'choice'}));
    } else if (radios.length === 0) {
      violations.push(
        violate(CODES.choiceOptionsMissing,
          '选择题一个选项都没有：要么索引里的 options 丢了，要么题型枚举静默退回了默认值。',
          {expected: 'choice', inputs: inputs.length}),
      );
    }
  }

  if (expected.mode === 'fillin' && !hasBox) {
    violations.push(violate(CODES.answerBoxMissing, '填空题该有一个作答框，页面上却没有。', {expected: 'fillin'}));
  }

  if (declared !== null && declared !== expected.mode) {
    violations.push(
      violate(CODES.answerModeMismatch,
        `页面自称的作答模式是 '${declared}'，判据算出来的是 '${expected.mode}'：组件宣称的和做的不一样。`,
        {declared, expected: expected.mode, reason: expected.reason}),
    );
  }

  return violations;
}

// --------------------------- 不变量 3：越界必须失败，绝不悄悄落到别的题上

/**
 * 验收 3（spec #1 第 34 条、契约 §4）：**队列是快照**——判完一道题的瞬间它进了冷却、
 * 从默认打印清单里消失，所以服务端现算的「第 i+1 题」会漂到别处去。队列只由 URL 带着走。
 *
 * 于是「i 越界了」这件事**必须显式失败**。原型有两处静默过滤，这条判据专门盯它们：
 *   · `proto/server.py:320-321` 的 `if pid in by_id`——队列里的题查不到就悄悄少一道；
 *   · `:933-936` 的 `continue`——同上。
 * 第九轮那条教训在这里同样适用（`docs/acceptance-log.md:284`）：越界必须明确失败。
 *
 * 失败**长什么样**也要查：必须是一个带 `data-error-code` 的面板，而且码要对得上
 * （把「越界」报成「队列为空」同样是坏事：人按错码去修错的地方）。
 */
export function checkQueue({html, search, problems = null}) {
  const violations = [];
  const parsed = parseRedoQuery(search);
  const tags = parseTags(html);
  const panel = tags.find((tag) => 'data-error-code' in tag.attrs);
  const article = tags.find((tag) => tag.name === 'article' && 'data-current-pid' in tag.attrs);
  const renderedPid = article ? article.attrs['data-current-pid'] : null;

  /** 期望出现的失败码：来自同一份实现（parseRedoQuery / resolveQueueItem），不在判据里重算。 */
  let expectedFailure = null;
  if (!parsed.ok) {
    expectedFailure = parsed.code;
  } else if (problems) {
    const resolution = resolveQueueItem(problems, parsed.queue, parsed.i);
    if (!resolution.ok) {
      expectedFailure = resolution.code;
    }
  }

  if (expectedFailure) {
    if (!panel) {
      violations.push(
        violate('queue_failure_not_rendered',
          `队列这份快照不成立（${expectedFailure}），页面上却没有一个明确的失败面板：` +
            '静默跳过正是这个项目反复被咬的那类失败。',
          {expected_code: expectedFailure, search: String(search || '')}),
      );
    } else if (panel.attrs['data-error-code'] !== expectedFailure) {
      violations.push(
        violate('queue_failure_wrong_code',
          `失败面板报的是 '${panel.attrs['data-error-code']}'，这件事的码应当是 '${expectedFailure}'。`,
          {declared: panel.attrs['data-error-code'], expected: expectedFailure}),
      );
    }

    if (renderedPid !== null) {
      violations.push(
        violate(renderedPid === (parsed.ok ? null : firstQueuePid(search))
          ? 'fell_back_to_the_first_problem'
          : 'other_problem_rendered',
        `这一批题不成立（${expectedFailure}），页面却渲染出了题卡 ${renderedPid}：` +
          '越界／缺失必须明确失败，绝不悄悄落到别的题上。',
        {rendered: renderedPid, expected_code: expectedFailure}),
      );
    }

    return violations;
  }

  if (panel) {
    violations.push(
      violate('spurious_queue_error',
        `队列是好的，页面上却摆了一个失败面板（data-error-code="${panel.attrs['data-error-code']}"）。`,
        {code: panel.attrs['data-error-code']}),
    );
  }

  if (problems) {
    const expectedPid = parsed.queue[parsed.i];
    if (renderedPid === null) {
      violations.push(
        violate('expected_problem_not_rendered',
          `队列第 ${parsed.i} 道是 ${expectedPid}，页面上却一道题都没渲染出来。`,
          {expected: expectedPid, search: String(search || '')}),
      );
    } else if (renderedPid !== expectedPid) {
      violations.push(
        violate(renderedPid === parsed.queue[0] && parsed.i > 0
          ? 'fell_back_to_the_first_problem'
          : 'other_problem_rendered',
        `队列第 ${parsed.i} 道是 ${expectedPid}，页面渲染的却是 ${renderedPid}。` +
          '「第 i 道」在 URL 里定住了，渲染出别的题就是悄悄换了一批。',
        {rendered: renderedPid, expected: expectedPid, i: parsed.i}),
      );
    }
  }

  return violations;
}

/** 队列里的第一道（只用来区分「回退到第一题」与「落到别的题上」）。 */
function firstQueuePid(search) {
  const raw = new URLSearchParams(String(search || '').replace(/^\?/, '')).get('queue');
  return raw ? raw.split(',')[0] : null;
}

// --------------------------------- 不变量 4：自检不得有副作用

/**
 * 验收 4（spec #1）：**「只读检查」不许变成一个会坏的写操作**。
 *
 * 原型的 `--selftest` 会写派生索引（`selftest → render_list → build_index →
 * INDEX_PATH.write_text`，`proto/server.py:973 → :637 → :172`）。后果有两层：
 * 跑一次自检就**生成了 `data/index.json`**；而在只读环境里，它报出来的错是
 * 「界面自检未通过」——**真因（不能写）与界面无关**，人会被引去修界面。
 *
 * 所以「自检有没有副作用」本身是一条**可跑的不变量**：把检查前后的目录快照比一比。
 * 快照的形状（由跑检查的那一侧生成）：
 *
 *     {"files": {"<相对路径>": {"size": <字节>, "mtime_ns": <整数>}}}
 *
 * JS 侧只负责**比**，不负责采（采集要在真跑子进程的前后各来一次，那件事在驱动侧）。
 */
export function checkNoSideEffects({before, after}) {
  const violations = [];
  const beforeFiles = before?.files || {};
  const afterFiles = after?.files || {};

  for (const path of Object.keys(afterFiles)) {
    if (!(path in beforeFiles)) {
      violations.push(
        violate('selftest_created_file', `自检创建了文件：${path}。只读检查不许写任何东西。`,
          {path, size: afterFiles[path]?.size ?? null}),
      );
    }
  }

  for (const path of Object.keys(beforeFiles)) {
    if (!(path in afterFiles)) {
      violations.push(violate('selftest_deleted_file', `自检删掉了文件：${path}。`, {path}));
      continue;
    }
    const was = beforeFiles[path] || {};
    const now = afterFiles[path] || {};
    if (was.size !== now.size || was.mtime_ns !== now.mtime_ns) {
      violations.push(
        violate('selftest_modified_file', `自检改动了文件：${path}（size ${was.size}→${now.size}）。`,
          {path, before: was, after: now}),
      );
    }
  }

  return violations;
}
