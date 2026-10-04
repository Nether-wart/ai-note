/**
 * 重做页（#7）里**能被离线断言**的那部分：队列、题型分化、题面图、提交形状。
 *
 * 这里一行 React 都没有，理由是纪律而不是审美：
 *   · **队列是快照**（契约 §4）：判完一道题的瞬间它进了冷却、从默认打印清单里消失，
 *     所以服务端现算的第 i+1 题会漂到别处去——队列只能由 URL 原样带着走。
 *   · **越界必须明确失败**（spec #1 第 34 条）。原型有两处静默过滤
 *     （`proto/server.py:320-321` 的 `if pid in by_id`、`:933-936` 的 `continue`）：
 *     队列里的题查不到就悄悄跳过、索引越界就悄悄落到第一题。这里全部返回带 `code`
 *     的失败对象——**#8 的界面不变量测试就是对着这些码断言的**。
 *   · **能不能自动判定只有服务端一份实现**（`server/autojudge.py`）。界面只读
 *     `auto_judge`／`screen_redo` 的读数，按它决定给不给作答框，**不自己再判一遍**。
 *   · **判定由服务做**：界面只提交 `{channel:"screen", answer}`（`screenPayload`），
 *     一个判定字段都不许自己填。
 */

/** 契约 §6.1 的两套候选总体；界面按开关**取**，不重算。 */
export const REDO_BASES = ['in_default_list', 'including_cooling'];

/** 题卡 id 的字符集与 `server/http.py` 的路径参数规则一致（不许出现 `..`）。 */
const PID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const NON_NEGATIVE_INT = /^\d+$/;

/** 服务端 `autojudge.REASONS` 的码；界面只用来**显示**，不用来判。 */
export const SOLVE_TYPE_REASON = 'solution_type';

function fail(code, message, details = {}, hint = null) {
  return {ok: false, code, message, details, hint};
}

// ------------------------------------------------------------ 队列在 URL 里

/**
 * `/redo?queue=p-a,p-b&i=1` → `{ok, queue, i, basis}`。
 *
 * 每一种坏输入都有自己的 `code`（`queue_missing` / `queue_empty` / `queue_malformed` /
 * `index_missing` / `index_invalid` / `index_out_of_range` / `basis_unknown`），
 * **一条都不许被忽略或修正**：静默补一个 `i=0` 就是「悄悄落到第一题」。
 */
export function parseRedoQuery(search) {
  const params = new URLSearchParams(String(search || '').replace(/^\?/, ''));

  const rawQueue = params.get('queue');
  if (rawQueue === null) {
    return fail('queue_missing', '这个链接里没有队列（queue=）：屏幕重做必须由 URL 带着这一批题。', {param: 'queue'},
      '从清单页点「开始屏幕重做」拿一条带 queue 的链接');
  }
  if (rawQueue.trim() === '') {
    return fail('queue_empty', '队列是空的（queue= 里没有题目）：要么这一批已经做完了，要么这条链接没带题目。',
      {param: 'queue'}, '回清单页再挑一批');
  }
  const queue = rawQueue.split(',');
  for (const pid of queue) {
    if (!PID_PATTERN.test(pid) || pid.includes('..')) {
      // 空元素、路径穿越、带空格……一律喊出来，绝不 filter 掉继续
      return fail('queue_malformed', `队列里的题卡 id 非法：'${pid}'`,
        {param: 'queue', bad: pid, allowed: '字母、数字、点、下划线与连字符；逗号分隔'});
    }
  }

  const rawIndex = params.get('i');
  if (rawIndex === null) {
    return fail('index_missing', '这个链接里没有第几题（i=）：不补默认值——补了就是悄悄落到第一题。',
      {param: 'i'}, '队列与索引一起从清单页的链接里来');
  }
  if (!NON_NEGATIVE_INT.test(rawIndex)) {
    return fail('index_invalid', `第几题（i）必须是一个非负整数，拿到的是 '${rawIndex}'`,
      {param: 'i', value: rawIndex, allowed: '非负整数'});
  }
  const i = Number(rawIndex);
  if (i >= queue.length) {
    return fail('index_out_of_range',
      `第 ${i} 题越界：这个队列里只有 ${queue.length} 道（索引 0..${queue.length - 1}）`,
      {param: 'i', value: i, count: queue.length},
      '绝不悄悄落到别的题上：改链接，或回清单页重新挑一批');
  }

  const basis = params.get('basis');
  if (basis !== null && !REDO_BASES.includes(basis)) {
    return fail('basis_unknown', `候选总体（basis）认不出：'${basis}'`,
      {param: 'basis', value: basis, allowed: REDO_BASES});
  }

  return {ok: true, queue, i, basis};
}

/** 反向：把队列与索引写回 URL。默认总体不写进 URL（服务会给 default_basis）。 */
export function buildRedoUrl({queue, i, basis = null}) {
  const ids = (queue || []).join(',');
  const query = [`queue=${encodeURIComponent(ids)}`, `i=${i}`];
  if (basis && basis !== 'in_default_list') {
    query.push(`basis=${encodeURIComponent(basis)}`);
  }
  return `/redo?${query.join('&')}`;
}

/** 队列里的第 i 道题在索引里查出来；查不到**明确失败**，不跳过它继续。 */
export function resolveQueueItem(problems, queue, i) {
  const pid = (queue || [])[i];
  if (pid === undefined) {
    return fail('index_out_of_range',
      `第 ${i} 题越界：这个队列里只有 ${(queue || []).length} 道`,
      {param: 'i', value: i, count: (queue || []).length});
  }
  const found = (problems || []).find((p) => p && p.id === pid);
  if (!found) {
    return fail('problem_not_in_index', `队列里的题在索引里找不到：${pid}`,
      {id: pid, i},
      '绝不跳过它继续：要么数据目录里少了这张卡，要么队列链接写错了');
  }
  return {ok: true, problem: found, pid, i};
}

/** 判完一道题就把它从队列里拿掉（第 21 条：这一批的顺序在 URL 里定住，别的题不动）。 */
export function removeAt(queue, i) {
  if (!Number.isInteger(i) || i < 0 || i >= (queue || []).length) {
    return fail('index_out_of_range', `第 ${i} 题越界：这个队列里只有 ${(queue || []).length} 道`,
      {param: 'i', value: i, count: (queue || []).length});
  }
  return {ok: true, removed: queue[i], queue: [...queue.slice(0, i), ...queue.slice(i + 1)]};
}

// --------------------------------------------------------- 输入随题型分化

/**
 * 给不给作答框、给什么样的：判据是**服务端的读数**，不是界面再判一遍。
 *
 *   `auto_judge.eligible === false` → 一个框都不给（服务已经说清为什么）
 *   `choice` → 选项按钮；`fillin` → 一个空；`solution` → 没有框
 *   `default` → **不退回选择题**（「题型枚举静默退回默认值」是这个项目真踩过的坑）
 */
export function answerMode(problem) {
  const judge = problem?.auto_judge || {};
  if (judge.eligible !== true) {
    return {mode: 'none', reason: judge.reason ?? 'not_auto_judgeable', reason_text: judge.reason_text ?? null};
  }
  switch (problem?.type) {
    case 'choice':
      return {mode: 'choice', options: problem.options || [], reason: null, reason_text: null};
    case 'fillin':
      return {mode: 'fillin', reason: null, reason_text: null};
    case 'solution':
      // 服务说可判、题型说解答：两份读数自相矛盾。按保守的一侧走——不给作答框。
      return {mode: 'none', reason: SOLVE_TYPE_REASON, reason_text: judge.reason_text ?? null};
    default:
      return {
        mode: 'none',
        reason: 'unknown_type',
        reason_text: `题型认不出：'${problem?.type}' ——不退回默认题型`,
      };
  }
}

// ---------------------------------------------------- 题面只能是擦除后的图

/**
 * `images.clean`（擦除手写后的题面）——**没有第二张可以替代**。
 *
 * 缺图时返回 `url: null`，**绝不回退到 `images.original`**：原图印着订正，把它摆在
 * 做题的人面前，重做就没有意义了（裁决 D4、spec #1 第 3 条与验收 2）。
 */
export function problemImage(problem) {
  const clean = problem?.images?.clean || null;
  if (clean) {
    return {url: clean, missing: false, message: null};
  }
  return {
    url: null,
    missing: true,
    message: '这道题没有擦除手写后的题面图（images.clean 是 null）：不回退到原图——原图印着订正。',
  };
}

// ------------------------------------------------ 页头的 N/M（照服务给的原话）

/**
 * 取服务算好的那一套 `screen_redo.bases`（契约 §6.1）。
 *
 * 界面按「显示冷却中的题」开关**取**数字，**一个都不重算**；开关指到服务没有的总体时
 * `known: false`（由界面显式报错），不悄悄退回默认那套。
 */
export function redoHeader(data, basisName = null) {
  const screenRedo = data?.screen_redo || {};
  const bases = screenRedo.bases || {};
  const basis = basisName || screenRedo.default_basis || null;
  const selected = basis ? bases[basis] : null;
  return {
    basis,
    known: Boolean(selected),
    available_bases: Object.keys(bases),
    basis_text: selected?.basis_text ?? null,
    ready: selected?.ready ?? 0,
    blocked_total: selected?.blocked_total ?? 0,
    not_auto_judgeable: selected?.not_auto_judgeable ?? {count: 0, by_reason: []},
    no_clean_image: selected?.no_clean_image ?? {count: 0, ids: [], message: null},
  };
}

/**
 * 清单页挑一批屏幕重做的题：队列 = 服务说 `screen_redo.ready` 的卡，顺序照服务排好的。
 *
 * `in_default_list` / `cooling` / `graduated` 都是服务给的**读数**，界面只做筛选，
 * 不重排、不重算冷却（契约 §4）。
 */
export function queueForProblems(problems, basisName) {
  return (problems || [])
    .filter((problem) => problem?.screen_redo?.ready === true)
    .filter((problem) =>
      basisName === 'including_cooling' ? problem.graduated !== true : problem.in_default_list === true,
    )
    .map((problem) => problem.id);
}

// -------------------------------------------------- 判定由服务做，界面只提交

/** 屏幕重做提交给服务的形状：**只有这两个键**（契约 §10.1）。 */
export function screenPayload(answer) {
  return {channel: 'screen', answer};
}

/**
 * 提交一次作答。`post(pid, payload)` 是注入的接缝（真实现走 `api.js` 的写端点）。
 *
 * 界面在这里**不做判定**：成功就把服务给的整封回应带出来，失败就把服务的原话
 * （`error.message` / `hint` / `reason`）带出来，**不给任何 verdict**。
 */
export async function submitScreenAnswer({pid, answer, post}) {
  try {
    const envelope = await post(pid, screenPayload(answer));
    return {ok: true, envelope};
  } catch (error) {
    return {
      ok: false,
      code: error?.code || 'unknown_error',
      reason: error?.reason ?? null,
      message: error?.message || String(error),
      hint: error?.hint ?? null,
      status: error?.status ?? 0,
    };
  }
}

/** #5 的回应（契约 §10.1）→ 结果区读数。**一律原样**，界面不翻译判定。 */
export function attemptResult(data) {
  const attempt = data?.attempt || {};
  const mastery = data?.mastery || {};
  return {
    at: attempt.at ?? null,
    channel: attempt.channel ?? null,
    verdict: attempt.verdict ?? null,
    source: attempt.source ?? null,
    confidence: attempt.confidence ?? null,
    provider: attempt.provider ?? null,
    model: attempt.model ?? null,
    note: mastery.note ?? attempt.note ?? null,
    credited: mastery.credited === true,
    streak: mastery.streak ?? 0,
    cooling: mastery.cooling ?? null,
    state: mastery.state ?? null,
    run_id: data?.run_id ?? null,
  };
}

/** 契约 §10.1 给 #7 的两句读数（故事 8/9）：计入掌握还是只热身。 */
export function creditLine({credited, streak} = {}) {
  if (credited === true) {
    return {kind: 'credited', text: `已计入 ${streak ?? 0}/2`};
  }
  return {kind: 'warmup', text: '只热身，不计入掌握'};
}
