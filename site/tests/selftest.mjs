#!/usr/bin/env node
/**
 * **屏幕重做页的界面自检**（#8）——替代原型的 `python3 proto/server.py --selftest`。
 *
 *     node site/tests/selftest.mjs
 *
 * 它回答的是「这一页现在还是不是对的」，判据是「渲染结果里有什么／没有什么」。三条性质：
 *
 *   1. **不靠浏览器、不靠人眼**：真组件用 `renderToStaticMarkup` 渲成 HTML，判据吃 HTML。
 *   2. **不碰私人数据**：夹具是手写的（`fixtures/problems.mjs`）。所以它在 worktree 里
 *      天然跑得起来——原型那版依赖 `data/problems`、`data/vocab`（都在 `.gitignore` 里），
 *      于是失败信息全是假信号。
 *   3. **没有副作用**：这一趟只读文件；而且它**把自己前后的目录快照比一遍**，
 *      确认自己真的没写任何东西（原型跑一次 `--selftest` 就生成了 `data/index.json`）。
 *
 * 每一趟还会跑一遍**故意做坏的夹具**（`fixtures/broken.js`）：判据必须对它们报警。
 * 理由写在验收记录里（`docs/acceptance-log.md:253`）：「一个从没失败过的检查，和一个
 * 从没通过过的检查，同样不可信」。判据哪天不再响了，这份自检自己会失败。
 *
 * 退出码：`0` 全过 · `1` 有界面不变量失败 · `2` 这次没跑完（环境缺依赖，**不是**界面坏了）
 */
import {
  checkAnswerBox,
  checkNoAnswerLeak,
  checkNoSideEffects,
  checkQueue,
  checkQuestionImage,
  leakCandidates,
} from './invariants.mjs';
import {createHarness, HarnessUnavailable} from './ssr.mjs';
import {snapshotTree} from './snapshot-tree.mjs';
import {redoHeader} from '../src/lib/redo.js';
import {API, CHOICE, CHOICE_NO_OPTIONS, FILLIN, INDEX, NO_CLEAN, SOLUTION, UNREVIEWED} from './fixtures/problems.mjs';

const checks = [];

function record(label, ok, detail = '') {
  checks.push({label, ok, detail});
  process.stdout.write(`${ok ? '✓' : '✗'} ${label}${detail ? `   ← ${detail}` : ''}\n`);
}

const codes = (violations) => violations.map((v) => v.code);

function expectClean(label, violations) {
  record(label, violations.length === 0, codes(violations).join(', '));
}

function expectAlarm(label, violations, expectedCodes) {
  const got = codes(violations);
  const ok = expectedCodes.every((code) => got.includes(code));
  record(label, ok, ok ? '' : `应当报 ${expectedCodes.join('/')}，实际 ${got.join('/') || '（一条都没报）'}`);
}

function expectCodes(label, violations, expectedCodes) {
  const got = codes(violations).sort();
  const want = [...expectedCodes].sort();
  record(label, JSON.stringify(got) === JSON.stringify(want), `实际 ${got.join('/') || '（空）'}`);
}

// ---- 不变量 4 的第一半：**先拍一张自己的快照**，跑完再拍一张来比 ----------------
// 快照在起台子之前拍：连"import 组件"这一步算在内，这一趟碰过什么都要看得见。
const SNAPSHOT_ROOTS = ['.'];
const before = snapshotTree(SNAPSHOT_ROOTS);

process.stdout.write('屏幕重做的界面自检（不靠浏览器、不读 data/、不写任何东西）\n');
process.stdout.write(`判据：site/tests/invariants.mjs · 夹具：site/tests/fixtures/problems.mjs\n\n`);

let harness;
try {
  harness = await createHarness();
} catch (error) {
  if (error instanceof HarnessUnavailable) {
    // 环境缺依赖**不是**界面坏了。原型把这两种失败混成一句「界面自检未通过」，
    // 于是人被引去修界面——那正是 #8 要消灭的病。
    process.stdout.write(`✗ 这次没跑完（环境）：${error.message}\n`);
    process.stdout.write('  这是环境缺依赖，**不是**界面不变量失败：装依赖用 ' +
      '`npm_config_cache="$PWD/.npm-cache" npm ci`（在 site/ 里）。\n');
    process.exit(2);
  }
  throw error;
}

process.stdout.write(`（渲染方式）${harness.note}\n\n`);

// ------------------------------------------------- 不变量 1／2／5：真组件

for (const fixture of [CHOICE, FILLIN]) {
  const html = harness.renderQuestion(fixture, {apiBase: API});
  expectClean(`不变量 1 · ${fixture.id}：题面是擦除手写后的图`,
    checkQuestionImage({html, pid: fixture.id, apiBase: API}));
  expectClean(`不变量 2 · ${fixture.id}：作答区随题型分化（${fixture.type}）`,
    checkAnswerBox({html, problem: fixture}));
  expectClean(`不变量 5 · ${fixture.id}：提交之前页面上没有答案`,
    checkNoAnswerLeak({html, problem: fixture}));
}

// 选项缺失的选择题（`options: []`）：**单输入框的填空回退**，不是死胡同（最终修复 pass）。
// 判据吃的是**真组件渲出来的 HTML**，所以它证明的是「页面上真的有一个能敲的输入框」，
// 而不是「逻辑上算出来应该有一个」（手写 HTML 只能证明判据自己会算）。
{
  const html = harness.renderQuestion(CHOICE_NO_OPTIONS, {apiBase: API});
  const inputs = html.match(/<input\b/g) || [];
  const disabledInputs = html.match(/<input\b[^>]*\bdisabled\b/g) || [];

  record('不变量 2 · 选项缺失的选择题：页面上有**一个**可敲的输入框（不是零个）',
    inputs.length === 1 && disabledInputs.length === 0, `inputs=${inputs.length}`);
  record('不变量 2 · 选项缺失的选择题：界面如实说明「选项缺失，请直接填写作答」',
    html.includes('data-options-empty="true"') && html.includes('请直接填写作答'));
  record('不变量 2 · 选项缺失的选择题：作答区是一个 fillin 表单，题面图仍然是擦除图',
    html.includes('data-answer-form="fillin"') && html.includes('data-answer-mode="fillin"'));
  expectClean('不变量 2 · 选项缺失的选择题：判据本身认这条回退（不再报选择题缺选项）',
    checkAnswerBox({html, problem: CHOICE_NO_OPTIONS}));
  expectClean('不变量 1 · 选项缺失的选择题：题面仍然是擦除手写后的图',
    checkQuestionImage({html, pid: CHOICE_NO_OPTIONS.id, apiBase: API}));
}

for (const fixture of [SOLUTION, UNREVIEWED]) {
  const html = harness.renderQuestion(fixture, {apiBase: API});
  expectClean(`不变量 2 · ${fixture.id}（${fixture.auto_judge.reason}）：不给作答框`,
    checkAnswerBox({html, problem: fixture}));
  expectClean(`不变量 1 · ${fixture.id}：题面仍然只有擦除图`,
    checkQuestionImage({html, pid: fixture.id, apiBase: API}));
  expectClean(`不变量 5 · ${fixture.id}：正解文本没有出现在页面上`,
    checkNoAnswerLeak({html, problem: fixture}));
}

// 缺擦除图的卡：**宁可没有图，也不许退回原图**（裁决 D4）。它不该出现在队列里
// （`screen_redo.ready === false`），所以这里查的是那条防御路径的表现。
{
  const html = harness.renderQuestion(NO_CLEAN, {apiBase: API});
  expectCodes('不变量 1 · 缺擦除图的卡：没有图，且**一张未擦除的图都没有**',
    checkQuestionImage({html, pid: NO_CLEAN.id, apiBase: API}), ['question_image_missing']);
  record('不变量 1 · 缺擦除图的卡：界面把「缺图」显式说出来（data-missing-clean）',
    html.includes('data-missing-clean="true"'));
  expectClean('不变量 2 · 缺擦除图的卡：一样不给作答框',
    checkAnswerBox({html, problem: NO_CLEAN}));
}

// ------------------------------------------------- 页头的 N／M（裁决 D3／D4）

{
  const headerHtml = harness.renderHeader(redoHeader(INDEX, 'in_default_list'));
  const basis = INDEX.screen_redo.bases.in_default_list;
  record('页头：显示「另有 N 道不能自动判定」并**逐个列出理由**（裁决 D3）',
    headerHtml.includes('data-count="not-auto-judgeable"') &&
      headerHtml.includes('data-reason="solution_type"') &&
      headerHtml.includes('data-reason="unreviewed"'),
    `count=${basis.not_auto_judgeable.count}`);
  record('页头：显示「另有 M 道缺擦除图」，文案照服务给的原话',
    headerHtml.includes('data-count="no-clean-image"') &&
      headerHtml.includes(basis.no_clean_image.message));
}

// ------------------------------------------------- 不变量 3：队列在 URL 里

// 队列从夹具里拼出来，不手抄 id：手抄会漂（第一版就把 p-fill02 写成了 p-fillin02，
// 于是"范围内"那一趟其实走的是"索引里找不到"的分支——好输入方向差点变成假的）。
const QUEUE = `${CHOICE.id},${FILLIN.id}`;

const QUEUE_CASES = [
  {label: '不变量 3 · 队列在范围内 → 渲染第 i 道', search: `?queue=${QUEUE}&i=1`},
  {label: '不变量 3 · i 越界 → 明确失败', search: `?queue=${QUEUE}&i=9`},
  {label: '不变量 3 · 队列里的题不在索引里 → 明确失败', search: `?queue=${CHOICE.id},p-gone99&i=1`},
  {label: '不变量 3 · 没有 i（缺省到 0 就是悄悄落到第一题）→ 明确失败', search: `?queue=${QUEUE}`},
  {label: '不变量 3 · 没有 queue → 明确失败', search: '?i=0'},
  {label: '不变量 3 · 队列为空 → 明确失败', search: '?queue=&i=0'},
];

for (const scenario of QUEUE_CASES) {
  const html = harness.renderPage({search: scenario.search, indexData: INDEX});
  expectClean(scenario.label, checkQueue({html, search: scenario.search, problems: INDEX.problems}));
}

// 再各自单独看一眼渲染结果，免得上面的检查在"什么都没渲染"时也照样通过
{
  const inRange = harness.renderPage({search: `?queue=${QUEUE}&i=1`, indexData: INDEX});
  record('不变量 3 · 范围内那一趟真的渲染出了第 i 道（不是空白页）',
    inRange.includes(`data-current-pid="${FILLIN.id}"`));
  const outOfRange = harness.renderPage({search: `?queue=${QUEUE}&i=9`, indexData: INDEX});
  record('不变量 3 · 越界那一趟渲染的是失败面板，且没有题卡',
    outOfRange.includes('data-error-code="index_out_of_range"') && !outOfRange.includes('data-current-pid='),
    'data-error-code="index_out_of_range"');
}

// --------------------------------------- 判据的报警能力：故意做坏的夹具

{
  const brokenOriginal = harness.broken.ShowsOriginal(CHOICE, {apiBase: API});
  expectAlarm('报警能力 · 题面被换成原图（含手写与订正）→ 判据必须响',
    checkQuestionImage({html: brokenOriginal, pid: CHOICE.id, apiBase: API}), ['question_image_not_clean']);

  const brokenBox = harness.broken.SolutionWithAnswerBox(SOLUTION);
  expectAlarm('报警能力 · 解答题出现了作答框 → 判据必须响',
    checkAnswerBox({html: brokenBox, problem: SOLUTION}), ['solution_has_answer_box']);

  const brokenLeak = harness.broken.LeaksAnswer(SOLUTION);
  expectAlarm('报警能力 · 页面上出现了正解文本 → 判据必须响',
    checkNoAnswerLeak({html: brokenLeak, problem: SOLUTION}), ['answer_text_leaked']);

  const brokenPage = harness.broken.FallsBackToFirst({queue: [CHOICE.id, FILLIN.id], apiBase: API});
  expectAlarm('报警能力 · 越界悄悄落到第一题 → 判据必须响',
    checkQueue({html: brokenPage, search: `?queue=${QUEUE}&i=9`, problems: INDEX.problems}),
    ['fell_back_to_the_first_problem']);
}

// ------------------------------------- 不变量 4：这一趟自己没有写任何东西

{
  const after = snapshotTree(SNAPSHOT_ROOTS);
  expectClean('不变量 4 · 这一趟没有创建／改动／删除任何文件（前后快照一致）',
    checkNoSideEffects({before, after}));
  record('不变量 4 · 快照真的拍到了东西（空快照比出来的"没变"不算数）',
    Object.keys(before.files).length > 10, `${Object.keys(before.files).length} 个文件`);

  // 反过来：判据对"生成了派生索引"这种真事必须响（原型的靶子）
  const created = {files: {...after.files, 'data/index.json': {size: 1, mtime_ns: 1}}};
  expectAlarm('报警能力 · 自检生成了 data/index.json → 判据必须响',
    checkNoSideEffects({before: after, after: created}), ['selftest_created_file']);
}

// ------------------------------------------------------------------ 报告

const failed = checks.filter((entry) => !entry.ok);
const coverage = leakCandidates(SOLUTION);

process.stdout.write('\n');
process.stdout.write(`本次跑了 ${checks.length} 条检查，失败 ${failed.length} 条。\n`);
process.stdout.write('本次没有检查的东西（说清楚，免得被当成保证）：\n');
process.stdout.write('  · 图片的**二进制内容**——判据只认 URL 与 data-image-kind。\n');
process.stdout.write('  · 单字母的标准答案（' +
  `${coverage.skipped.map((s) => `${s.field}=${JSON.stringify(s.text)}`).join('、')}` +
  '）的逐字判据：它由「提交之前页面上没有判定节点」兜底（spec #1 Testing Decisions 第 2 条）。\n');
process.stdout.write('  · 路由本身（`@docusaurus/router` 换成了桩，只注入 search）。\n');
process.stdout.write('  · 真实数据（`data/` 一眼都没看：夹具是手写的）。\n');
process.stdout.write('  · 图片端点是否真的存在、可读（那是 server 那一侧的事）。\n');

if (failed.length > 0) {
  process.stdout.write('\n界面自检未通过：\n');
  for (const entry of failed) {
    process.stdout.write(`  ✗ ${entry.label}${entry.detail ? `   ← ${entry.detail}` : ''}\n`);
  }
  process.exit(1);
}
process.stdout.write('\n界面自检通过：屏幕重做页的四条不变量都成立，而且判据对坏夹具会响。\n');
