/**
 * 切分修正界面（#14）纯逻辑的测试：`node --test site/tests/`。
 *
 * 断言的是**外部行为**（输入 → 读数）：重切三态怎么显示、两套坐标怎么换算、
 * 修正请求的形状。夹具是**手写的**，不来自 `data/`（真实题卡不被测试碰）。
 *
 * 坐标那几条对着**真实数据可复算的一组数**（`p-20260101-aaaaaa`，只读核对过），
 * 期望值由纸笔算出、不是从实现里抄的——否则那些断言只是把实现又说了一遍。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  EDIT_ACTIONS,
  PROBLEM_TYPES,
  RESEGMENT_STATES,
  blockBoxToDisplay,
  blockView,
  editOutcome,
  editPayload,
  editsRequest,
  keepLabel,
  maskBoxesOntoPage,
  maskBoxOntoPage,
  maskBoxToCropDisplay,
  pageBoxToPixels,
  pageView,
  readBox,
  resegmentCardWarnings,
  resegmentRows,
  resegmentSummary,
  unionBox,
} from '../src/lib/split.js';

// ---------------------------------------------------------------- 夹具

/** 真实数据那一组数：卡上 bbox_norm 是整页 xywh，clean.boxes_norm 是裁剪图 xywh。 */
const REAL_PAGE_BOX = [0.02, 0.12, 0.76, 0.28];
const REAL_MASK_IN_CROP = [0.58, 0.05, 0.08, 0.22];

function problemBlock(overrides = {}) {
  return {
    id: 'b1',
    bbox_norm: [0.02, 0.12, 0.76, 0.28],
    bbox_px: [3, 20, 541, 79],
    card_id: 'p-20260101-aaaaaa',
    keep: true,
    question_no: 7,
    problem_type: 'choice',
    ...overrides,
  };
}

/** 一个 `classify_resegment` 形状的重切报告（只放这层要用到的字段）。 */
function resegmentReport(overrides = {}) {
  return {
    page_id: 'aaaabbbbcccc',
    blocks: [
      {id: 'b1', bbox_norm: REAL_PAGE_BOX, card_id: 'p-20260101-aaaaaa', keep: true},
      {id: 'b2', bbox_norm: [0.02, 0.44, 0.76, 0.2], card_id: null, keep: null},
    ],
    matches: [
      {block_id: 'b1', state: 'kept', matched_from: 'b1', iou: 0.94, contain: 1.0},
      {block_id: 'b2', state: 'new', matched_from: null, iou: null, contain: null},
    ],
    removed: [],
    warnings: [],
    summary: {kept: 1, new: 1, replaced: 0, removed: 0, needs_human: false,
      human_work_checked: false},
    wrote_cards: false,
    wrote_page: false,
    ran: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------- 坐标：形状

test('a box that cannot be read is null and never a zero box', () => {
  // 「不知道」与「在左上角」是两件事：零框会静默画在角上
  assert.equal(readBox(null), null);
  assert.equal(readBox([0.1, 0.2, 0.3]), null);
  assert.equal(readBox([0.1, 0.2, 0, 0.3]), null);
  assert.equal(readBox([0.1, 0.2, -0.3, 0.3]), null);
  assert.equal(readBox(['x', 0.2, 0.3, 0.3]), null);
  assert.deepEqual(readBox([0.1, 0.2, 0.3, 0.4]), [0.1, 0.2, 0.3, 0.4]);
});

test('the block box is drawn on the page photo by scaling only', () => {
  // 块本来就是整页坐标 → 画在整页照片上只做缩放（1000x500 的显示尺寸）
  assert.deepEqual(blockBoxToDisplay([0.1, 0.2, 0.3, 0.4], 1000, 500).map((v) => Number(v.toFixed(6))),
    [100, 100, 300, 200]);
  // 没有显示尺寸就画不出来，不许猜一个默认值
  assert.equal(blockBoxToDisplay([0.1, 0.2, 0.3, 0.4], 0, 500), null);
  assert.equal(pageBoxToPixels([0.1, 0.2, 0.3, 0.4], 0, 500), null);
});

test('the mask box is drawn on the crop image by scaling only', () => {
  // 掩膜本来就是裁剪图坐标 → 画在裁剪图上同样只做缩放
  // （0.58*800 在浮点下是 463.99999999999994，所以按位数比）
  assert.deepEqual(maskBoxToCropDisplay(REAL_MASK_IN_CROP, 800, 400).map((v) => Number(v.toFixed(6))),
    [464, 20, 64, 88]);
});

// ---------------------------------------------------------------- 坐标：两套基准

test('a mask box drawn on the page photo is converted, not used raw', () => {
  // 换算：x = 0.02 + 0.58*0.76 = 0.4608、y = 0.12 + 0.05*0.28 = 0.134
  //       w = 0.08*0.76 = 0.0608、h = 0.22*0.28 = 0.0616
  const onPage = maskBoxOntoPage(REAL_MASK_IN_CROP, REAL_PAGE_BOX);

  assert.deepEqual(onPage.map((v) => Number(v.toFixed(4))), [0.4608, 0.134, 0.0608, 0.0616]);
  // 反例：不换算就当整页坐标用，x 会落在 0.58 —— 差 0.12 个页宽（静默错位）
  assert.notEqual(onPage[0], REAL_MASK_IN_CROP[0]);
});

test('a mask that covers the whole crop covers exactly its block on the page', () => {
  // 换算是线性映射：两个端点必须对上
  assert.deepEqual(maskBoxOntoPage([0, 0, 1, 1], REAL_PAGE_BOX).map((v) => Number(v.toFixed(6))),
    REAL_PAGE_BOX);
});

test('a mask dragged outside its block is clamped onto the block', () => {
  // 手工掩膜是人在裁剪图上拖的，拖出界是常事；画到页上会盖住邻居
  const clamped = maskBoxOntoPage([2.0, 2.0, 0.2, 0.2], REAL_PAGE_BOX);
  assert.equal(clamped, null, '整个在块外面的框画不出来，给 null 而不是零框');

  const partial = maskBoxOntoPage([0.8, 0.9, 0.5, 0.5], REAL_PAGE_BOX);
  assert.deepEqual(partial.map((v) => Number(v.toFixed(3))), [0.628, 0.372, 0.152, 0.028]);
  // 不裁的话它会伸出块外
  const unclamped = maskBoxOntoPage([0.8, 0.9, 0.5, 0.5], REAL_PAGE_BOX, {clamp: false});
  assert.ok(unclamped[0] + unclamped[2] > REAL_PAGE_BOX[0] + REAL_PAGE_BOX[2]);
});

test('a list of mask boxes keeps its positions and gives null where it cannot draw', () => {
  const boxes = maskBoxesOntoPage([REAL_MASK_IN_CROP, null, [0, 0, 1, 1]], REAL_PAGE_BOX);

  assert.equal(boxes.length, 3, '位置要对得上，才知道是哪一个掩膜没画出来');
  assert.equal(boxes[1], null);
  assert.deepEqual(boxes[2].map((v) => Number(v.toFixed(6))), REAL_PAGE_BOX);
});

test('a mask box on the page stays inside its block', () => {
  // 换算对不对的硬判据：换算之后它必须仍在块的框里
  const onPage = maskBoxOntoPage(REAL_MASK_IN_CROP, REAL_PAGE_BOX);
  const [px, py, pw, ph] = REAL_PAGE_BOX;
  const [x, y, w, h] = onPage;

  assert.ok(x >= px - 1e-9 && y >= py - 1e-9);
  assert.ok(x + w <= px + pw + 1e-9 && y + h <= py + ph + 1e-9);
});

// ---------------------------------------------------------------- 合并的并集

test('merging two blocks takes the union of their boxes', () => {
  // b1 的 y 区间 [0.02,0.22]、b2 的是 [0.24,0.44] → y0=0.02、y1=0.44、h=0.42
  const union = unionBox([[0.02, 0.02, 0.9, 0.2], [0.02, 0.24, 0.9, 0.2]]);

  assert.deepEqual(union.map((v) => Number(v.toFixed(2))), [0.02, 0.02, 0.9, 0.42]);
  assert.equal(unionBox([null, [1, 1, 0, 1]]), null, '全读不出来 → null，不是零框');
});

// ---------------------------------------------------------------- 重切三态（验收 1）

test('the three states are labelled in words the operator can read', () => {
  // 「保留」是验收 1 那条目标句的原话
  assert.equal(RESEGMENT_STATES.kept.label, '保留');
  assert.equal(RESEGMENT_STATES.new.label, '新增');
  assert.equal(RESEGMENT_STATES.replaced.label, '替换');
  assert.ok(RESEGMENT_STATES.kept.detail.includes('id 不变'));
});

test('resegment rows show kept vs new per block with its card id', () => {
  const rows = resegmentRows(resegmentReport());

  assert.deepEqual(rows.map((r) => r.state), ['kept', 'new']);
  assert.deepEqual(rows.map((r) => r.label), ['保留', '新增']);
  // 已入库的那一块**卡片 id 不变**（验收 1 的后半）
  assert.equal(rows[0].card_id, 'p-20260101-aaaaaa');
  assert.equal(rows[1].card_id, null);
  assert.equal(rows[0].matched_from, 'b1');
  assert.equal(rows[0].iou, 0.94);
});

test('the summary counts all three states and never hides the zero one', () => {
  const summary = resegmentSummary(resegmentReport());

  // 只显示「重切完成」正是这条验收要防的：「替换」那一栏必须显式出现
  assert.deepEqual(summary.counts, {kept: 1, new: 1, replaced: 0, removed: 0});
  assert.equal(summary.line, '保留 1 · 新增 1 · 替换 0');
});

test('the summary says when a block went missing and a human should look', () => {
  const summary = resegmentSummary(resegmentReport({
    removed: [{id: 'b9', card_id: 'p-20260101-bbbbbb'}],
    summary: {kept: 0, new: 1, replaced: 0, removed: 1, needs_human: true,
      human_work_checked: true},
  }));

  assert.equal(summary.counts.removed, 1);
  assert.ok(summary.line.includes('消失 1'));
  assert.equal(summary.needs_human, true);
});

test('the ui does not decide for itself whether a human must look', () => {
  // 「要不要人看一眼」由**服务**判（classify_resegment 的 needs_human），界面不自己判一遍
  const quiet = resegmentSummary(resegmentReport({
    removed: [{id: 'b9', card_id: null}], summary: {kept: 0, new: 0, replaced: 0, removed: 1},
  }));

  assert.equal(quiet.needs_human, false);
});

test('resegment says so when segmentation did not run at all', () => {
  // 切分不可用：blocks 是 null（不是 []），界面要说得出来
  const summary = resegmentSummary({
    segmentation: 'unavailable', ran: false, blocks: null, matches: null,
    summary: null, wrote_cards: false, wrote_page: false,
  });

  assert.equal(summary.segmentation_unavailable, true);
  assert.equal(summary.ran, false);
  assert.deepEqual(resegmentRows({matches: null, blocks: null}), []);
});

test('resegment never claims it wrote cards or the page', () => {
  // 重切**不写题卡也不写页**（spec #2 原话：返回的是逐块对照）
  const summary = resegmentSummary(resegmentReport());

  assert.equal(summary.wrote_cards, false);
  assert.equal(summary.wrote_page, false);
});

test('card-level warnings from the service are shown verbatim', () => {
  const warnings = resegmentCardWarnings(resegmentReport({
    warnings: [
      {code: 'resegment_card_human_work', id: 'p-20260101-aaaaaa', level: 'warning',
        message: '块 b1 对应的卡片有审核过的字段 → 这次重切只给对照，不会改写它'},
      {code: 'block_without_box', id: null, level: 'warning', message: '边界读不出来'},
    ],
  }));

  assert.equal(warnings.length, 1, '只挑卡级的两条，别的码不在这里显示');
  assert.equal(warnings[0].id, 'p-20260101-aaaaaa');
  // 照服务给的原话显示，界面不重拼一句
  assert.ok(warnings[0].message.includes('不会改写它'));
});

// ---------------------------------------------------------------- 修正请求的形状

test('every editable action has a chinese label', () => {
  for (const action of Object.keys(EDIT_ACTIONS)) {
    assert.ok(EDIT_ACTIONS[action].length > 0, `${action} 要有中文原话`);
  }
  assert.deepEqual(Object.keys(EDIT_ACTIONS).sort(),
    ['drop', 'keep', 'merge', 'move', 'question_no', 'split', 'type']);
});

test('a move payload carries only the box the human dragged', () => {
  const payload = editPayload('move', {block_id: 'b1', bbox_norm: [0, 0, 1, 0.25]});

  assert.deepEqual(payload, {action: 'move', block_id: 'b1', bbox_norm: [0, 0, 1, 0.25]});
  // 判定字段一个都不许由客户端给
  for (const forbidden of ['verdict', 'source', 'confidence', 'rule', 'keep']) {
    assert.ok(!(forbidden in payload), `界面不许提交 ${forbidden}`);
  }
});

test('a move payload with an unusable box is refused before it is sent', () => {
  assert.throws(() => editPayload('move', {block_id: 'b1', bbox_norm: [0, 0, 0, 0.25]}),
    /bbox_norm/);
});

test('a merge needs at least two blocks', () => {
  assert.throws(() => editPayload('merge', {block_ids: ['b1']}), /两块/);
  assert.deepEqual(editPayload('merge', {block_ids: ['b1', 'b2']}),
    {action: 'merge', block_ids: ['b1', 'b2']});
});

test('a split carries the new boxes and only passes question numbers when given', () => {
  const boxes = [[0.02, 0.02, 0.9, 0.19], [0.02, 0.23, 0.9, 0.19]];
  const without = editPayload('split', {block_id: 'b1', boxes});
  // 题号**不给就是不猜**：服务会报 page_split_question_no_unset，界面显示那一句
  assert.ok(!('question_numbers' in without));
  assert.deepEqual(editPayload('split', {block_id: 'b1', boxes, question_numbers: [7, 8]})
    .question_numbers, [7, 8]);

  assert.throws(() => editPayload('split', {block_id: 'b1', boxes: [boxes[0]]}), /两个/);
});

test('a question number is either a positive integer or left blank', () => {
  assert.deepEqual(editPayload('question_no', {block_id: 'b1', question_no: 17}),
    {action: 'question_no', block_id: 'b1', question_no: 17});
  // 留空是允许的（看不清就不填）——但不许硬转一个号出来
  assert.equal(editPayload('question_no', {block_id: 'b1', question_no: null}).question_no, null);
  assert.throws(() => editPayload('question_no', {block_id: 'b1', question_no: 17.5}), /正整数/);
  assert.throws(() => editPayload('question_no', {block_id: 'b1', question_no: '17'}), /正整数/);
});

test('a problem type outside the enum is refused before it is sent', () => {
  assert.deepEqual(editPayload('type', {block_id: 'b1', problem_type: 'solution'}),
    {action: 'type', block_id: 'b1', problem_type: 'solution'});
  // 还没定也是允许的（与「解答题」不是一回事）
  assert.equal(editPayload('type', {block_id: 'b1', problem_type: null}).problem_type, null);
  assert.throws(() => editPayload('type', {block_id: 'b1', problem_type: 'multiple_choice'}),
    /枚举/);
  assert.deepEqual(Object.keys(PROBLEM_TYPES), ['choice', 'fillin', 'solution']);
});

test('switching keep or drop carries nothing but the block id', () => {
  // **改的是决策，不是另立一套「收不收」的判断**：请求里只有一个块 id
  assert.deepEqual(editPayload('drop', {block_id: 'b3'}), {action: 'drop', block_id: 'b3'});
  assert.deepEqual(editPayload('keep', {block_id: 'b3'}), {action: 'keep', block_id: 'b3'});
});

test('an unknown action is refused with the list of the allowed ones', () => {
  assert.throws(() => editPayload('rotate', {block_id: 'b1'}), /rotate/);
  assert.throws(() => editPayload('rotate', {block_id: 'b1'}), /move/);
});

test('an edits request can be a dry run', () => {
  const edits = [editPayload('drop', {block_id: 'b3'})];

  assert.deepEqual(editsRequest(edits), {dry_run: false, edits});
  assert.equal(editsRequest(edits, {dryRun: true}).dry_run, true);
});

// ---------------------------------------------------------------- 修正的结果

test('the outcome of an edit says what changed and what was a no-op', () => {
  const outcome = editOutcome({
    changed: true,
    dry_run: false,
    wrote_cards: false,
    wrote_page: true,
    edits: [
      {action: 'move', changed: true, blocks_changed: ['b1'], blocks_removed: []},
      {action: 'question_no', changed: false, blocks_changed: [], blocks_removed: []},
    ],
    warnings: [{code: 'page_block_edit_noop', level: 'hint', id: null, message: '本来就…'}],
  });

  assert.equal(outcome.changed, true);
  assert.equal(outcome.applied, 1);
  assert.equal(outcome.noop, 1, '「没动」要看得见，不许显示成成功');
  assert.deepEqual(outcome.rows.map((r) => r.label), ['拖边界', '改题号']);
  assert.equal(outcome.rows[1].changed, false);
  assert.equal(outcome.wrote_page, true);
  assert.equal(outcome.wrote_cards, false, '修正不写题卡（那是「入库」，归 #15）');
});

// ---------------------------------------------------------------- 页的视图

test('the page view counts the three states of keep apart', () => {
  const view = pageView({
    id: 'aaaabbbbcccc', image: 'aaaabbbbcccc.png',
    origin: {original_file: '2.png', sheet: null, page_number: null},
    blocks: [
      problemBlock({id: 'b1', keep: true}),
      problemBlock({id: 'b2', keep: false}),
      problemBlock({id: 'b3', keep: null}),
    ],
  });

  // **待定不是「不收」**（静默丢题是 spec #2 最怕的失败）
  assert.deepEqual(view.counts, {blocks: 3, kept: 1, dropped: 1, pending: 1});
  assert.equal(view.not_kept.length, 2, '没入库的题要列出来（#12 验收 1）');
  assert.deepEqual(view.not_kept.map((r) => r.keep_cn), ['不收', '待定']);
});

test('a block view keeps the two coordinate systems apart', () => {
  const view = blockView(problemBlock(), {maskBoxes: [REAL_MASK_IN_CROP]});

  // 块框：本来就是整页坐标，原样给（画在整页照片上）
  assert.deepEqual(view.page_box, REAL_PAGE_BOX);
  // 掩膜：裁剪图坐标原样给（画在题面裁剪图上）
  assert.deepEqual(view.masks_in_crop, [REAL_MASK_IN_CROP]);
  // 掩膜投到整页上要**换算**——两者必须不同，否则就是混用了
  assert.notDeepEqual(view.masks_on_page[0], view.masks_in_crop[0]);
  assert.deepEqual(view.masks_on_page[0].map((v) => Number(v.toFixed(4))),
    [0.4608, 0.134, 0.0608, 0.0616]);
});

test('a block without a usable box draws no masks rather than guessing', () => {
  const view = blockView(problemBlock({bbox_norm: [0, 0, 0, 0.2]}),
    {maskBoxes: [REAL_MASK_IN_CROP]});

  assert.equal(view.page_box, null);
  assert.deepEqual(view.masks_on_page, []);
});

test('the keep label has three values, not two', () => {
  assert.equal(keepLabel(true), '收');
  assert.equal(keepLabel(false), '不收');
  assert.equal(keepLabel(null), '待定');
  assert.equal(keepLabel(undefined), '待定');
});

test('a block view carries the auditable decision and its type in chinese', () => {
  const view = blockView(problemBlock({
    decision: {keep: true, rule: 'error_trace', source: 'model', semantics: 'correction',
      reason: '红笔是订正（改错）（表示这道题错了）→ 收'},
  }));

  assert.equal(view.problem_type_cn, '选择题');
  assert.equal(view.decision.rule, 'error_trace');
  assert.ok(view.decision.reason.includes('红笔是订正'));
});

test('the ui does not judge for itself whether a problem can be auto-judged', () => {
  // 唯一实现在 server/autojudge.py；界面只把服务给的读数带出来
  const readout = pageView({blocks: []}) && null;   // 只是明确这一层没有判定逻辑
  assert.equal(readout, null);
  // blockView 里没有任何「能不能自动判定」的字段
  const view = blockView(problemBlock());
  assert.ok(!('auto_judge' in view));
  assert.ok(!('can_auto_judge' in view));
});
