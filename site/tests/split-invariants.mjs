/**
 * 切分修正页的**界面不变量**（#14）：把「渲染结果里有什么／没有什么」写成可跑的判据。
 *
 * 与 #8 的三条纪律同源：
 *   1. **不靠浏览器、不联网**：判据吃一个 HTML 字符串；真渲染由 `ssr.mjs` 用
 *      React 的 `renderToStaticMarkup` 做（服务端渲染，不开浏览器）。
 *   2. **判据对着真实 HTML 写**，不写 CSS 选择器——认 `data-*` 与 `src` 的真实写法。
 *   3. **不信声明，只信 URL**：`data-image-kind="page"` 是组件自己贴的标签，
 *      所以判据同时看**声明**与**真实 URL**。
 *
 * 每条判据返回**违规数组**（空 = 这条不变量成立）。每条违规带机器可读的 `code`。
 */

/** 违规码表。测试与自检都对着这些码断言。 */
export const CODES = Object.freeze({
  // 验收 1：重切三态必须逐块显示
  resegmentStatesMissing: 'resegment_states_missing',
  resegmentCountsMissing: 'resegment_counts_missing',
  replacedCountHidden: 'replaced_count_hidden',
  // 验收 3：修正写回页文件（界面不许自称只存在内存里）
  editWritebackNotStated: 'edit_writeback_not_stated',
  // 不许静默
  warningsSwallowed: 'warnings_swallowed',
  // 坐标：两套基准要分开说
  coordinatesConflated: 'coordinates_conflated',
  // 待定不许显示成不收
  pendingShownAsDropped: 'pending_shown_as_dropped',
  // 界面不许自己判「能不能自动判定」
  autojudgeJudgedInUi: 'autojudge_judged_in_ui',
  // 页 id 缺失要明确失败
  pageIdMissingNotRefused: 'page_id_missing_not_refused',
});

function violate(code, message, details = {}) {
  return {code, message, details};
}

/** HTML 里的属性值（够用就好：属性都是双引号包起来的）。 */
function attr(html, name) {
  const found = [...html.matchAll(new RegExp(`${name}="([^"]*)"`, 'g'))].map((m) => m[1]);
  return found;
}

function text(html) {
  return html.replace(/<[^>]*>/g, ' ');
}

/**
 * 判据 1（**验收 1**）：重切报告渲染出来时，**逐块的三态**必须都在。
 *
 * 只显示一句「重切完成」是不够的——「已入库的块被替换了」在那种界面上看不见，
 * 而那正是这条验收要防的失败。
 */
export function checkResegmentStates(html) {
  const violations = [];
  if (!/data-resegment/.test(html)) return violations;   // 没重切过就不管

  if (!/data-resegment-counts/.test(html)) {
    violations.push(violate(CODES.resegmentCountsMissing,
      '重切对账没有显示三档计数', {}));
  }
  // 「替换 0」也必须出现：三档一档都不许省
  if (/data-resegment-counts/.test(html) && !/替换/.test(text(html))) {
    violations.push(violate(CODES.replacedCountHidden,
      '三档计数里看不到「替换」那一栏（恒为 0 也必须显式出现）', {}));
  }

  const states = attr(html, 'data-state');
  if (states.length === 0 && !/data-segmentation="unavailable"/.test(html)) {
    violations.push(violate(CODES.resegmentStatesMissing,
      '重切对账没有逐块给出状态（data-state）', {}));
  }
  for (const state of states) {
    if (!['kept', 'new', 'replaced'].includes(state)) {
      violations.push(violate(CODES.resegmentStatesMissing,
        `逐块状态出现了枚举外的值：${state}`, {state}));
    }
  }
  return violations;
}

/**
 * 判据 2（**验收 3**）：修正之后界面必须**说清写回页文件了没有**。
 *
 * 「切分结果与收入决策一旦只存在于界面的内存里，『漏了一题』就永远查不出来」
 * （spec #2）。所以界面上要能看见「已写回页文件 / 页文件没写（预演）」这句话。
 */
export function checkWritebackStated(html) {
  const violations = [];
  if (!/data-edit-outcome/.test(html)) return violations;
  const body = text(html);
  if (!/页文件/.test(body)) {
    violations.push(violate(CODES.editWritebackNotStated,
      '修正结果里没说清页文件写了没有', {}));
  }
  if (!/题卡/.test(body)) {
    violations.push(violate(CODES.editWritebackNotStated,
      '修正结果里没说清题卡动没动（修正不写题卡，要明说）', {}));
  }
  return violations;
}

/**
 * 判据 3（**不许静默**）：服务给的每条警告都要有一个显示位（带 `data-warning-code`）。
 */
export function checkWarningsShown(html, expectedCodes = []) {
  const shown = attr(html, 'data-warning-code');
  return expectedCodes
    .filter((code) => !shown.includes(code))
    .map((code) => violate(CODES.warningsSwallowed,
      `服务给的警告 ${code} 没有显示出来`, {code}));
}

/**
 * 判据 4（**D5 的坐标坑**）：两套坐标必须分开说，不许混成一句。
 *
 * 「块框画在整页照片上」与「掩膜框画在题面裁剪图上」用的是两套基准；
 * 界面上要把这两件事说清楚，否则改错东西的人不知道自己改的是哪一套。
 */
export function checkCoordinateBases(html) {
  const violations = [];
  const body = text(html);
  if (!/整页/.test(body)) {
    violations.push(violate(CODES.coordinatesConflated,
      '界面没说清块框画在**整页**照片上', {}));
  }
  if (!/裁剪图/.test(body)) {
    violations.push(violate(CODES.coordinatesConflated,
      '界面没说清掩膜框是**裁剪图**坐标（两套基准必须分开说）', {}));
  }
  return violations;
}

/**
 * 判据 5：**「待定」不许显示成「不收」**（静默丢题是 spec #2 最怕的失败）。
 *
 * 服务把「统计读不出来」给成 `keep: null`，界面必须显示成待定那一档。
 */
export function checkPendingNotDropped(html) {
  const violations = [];
  const keeps = attr(html, 'data-keep');
  for (const value of keeps) {
    if (!['true', 'false', 'pending'].includes(value)) {
      violations.push(violate(CODES.pendingShownAsDropped,
        `块的去留出现了枚举外的值：${value}`, {value}));
    }
  }
  // 页头那三档计数里，「待定」与「不收」必须是两个数
  const header = (html.match(/data-page-path[^>]*>([^<]*)</) || [])[1] || '';
  if (header && !/待定/.test(header)) {
    violations.push(violate(CODES.pendingShownAsDropped,
      '页头的三档计数里少了「待定」那一档', {}));
  }
  return violations;
}

/**
 * 判据 6：界面**不许自己判「能不能自动判定」**（唯一实现在 `server/autojudge.py`）。
 */
export function checkAutojudgeNotInUi(html) {
  const violations = [];
  // 界面可以**显示**服务给的 auto_judge 读数，但不许出现自己算的判定标记
  if (/data-auto-judged-by="ui"/.test(html)) {
    violations.push(violate(CODES.autojudgeJudgedInUi,
      '界面自己标了一个自动判定结论（唯一实现在 server/autojudge.py）', {}));
  }
  return violations;
}

/** 判据 7：整页照片的 URL 要真的是**这一页**的照片端点。 */
export function checkPageImage(html, pageId) {
  const violations = [];
  const srcs = attr(html, 'src');
  const wanted = `/api/page/${encodeURIComponent(pageId)}/image`;
  if (!srcs.some((src) => src.includes(wanted))) {
    violations.push(violate(CODES.coordinatesConflated,
      `整页照片的 src 不是这一页的图片端点（应当是 ${wanted}）`, {srcs}));
  }
  return violations;
}

/** 把一页渲染出来时该过的全部判据（自检与测试共用一份）。 */
export function checkSplitPage(html, {pageId = 'aaaabbbbcccc', warningCodes = []} = {}) {
  return [
    ...checkResegmentStates(html),
    ...checkWritebackStated(html),
    ...checkWarningsShown(html, warningCodes),
    ...checkCoordinateBases(html),
    ...checkPendingNotDropped(html),
    ...checkAutojudgeNotInUi(html),
    ...checkPageImage(html, pageId),
  ];
}
