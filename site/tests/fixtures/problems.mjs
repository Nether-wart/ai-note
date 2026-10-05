/**
 * 界面不变量的夹具（#8）：**手写的题卡记录**，不来自真实数据目录。
 *
 * 形状照契约 §3.1 的 Problem 记录与 §6.1 的 `screen_redo` 读数抄，字段名一个不差——
 * 判据是"对着真实 HTML 写"的，夹具也得"对着真实记录写"，否则判据会在真数据上失灵。
 *
 * 两条纪律：
 *   · **不碰 `data/`**：真实题卡（`data/problems/*.json`）在 `.gitignore` 里，
 *     自检若依赖它，在 worktree 里天然跑不了，失败信息还全是假信号（原型的病）。
 *   · **`screen_redo` 的汇总照抄服务算好的形状**，自检**不重算它**：一处实现，
 *     一个真源（契约 §6.1 第 1 条）。这里抄的是服务会给的那一份读数。
 */

export const API = 'http://127.0.0.1:8765';

/** 一张契约 §3.1 形状的卡（只留界面会用到的字段）。 */
export function problem(overrides = {}) {
  const id = overrides.id || 'p-choice01';
  return {
    id,
    created_at: '2026-10-04T08:00:00+00:00',
    type: 'choice',
    type_cn: '选择题',
    transcript: '1. 夹具题面：下列哪个选项正确？',
    options: [
      {label: 'A', text: '甲'},
      {label: 'B', text: '乙'},
    ],
    original_answer: 'B',
    correction: null,
    standard_answer: 'A',
    correct_solution: null,
    topics: ['集合'],
    error_causes: [],
    review: 'reviewed',
    mastery: {state: 'in_pool', streak: 0, last_attempt_at: null},
    streak: 0,
    graduated: false,
    cooling: false,
    cooldown_days: 0,
    in_default_list: true,
    sort_key: '2026-10-04T08:00:00+00:00',
    cells: 1,
    images: {
      original: `/api/problem/${id}/image/original`,
      clean: `/api/problem/${id}/image/clean`,
      mask: `/api/problem/${id}/image/mask`,
    },
    has_clean: true,
    auto_judge: {eligible: true, reason: null, reason_text: null},
    screen_redo: {ready: true, blockers: [], blocker_text: []},
    warnings: [],
    ...overrides,
  };
}

/** 选择题：屏幕可做。 */
export const CHOICE = problem();

/** 填空题：屏幕可做。 */
export const FILLIN = problem({
  id: 'p-fill02',
  type: 'fillin',
  type_cn: '填空题',
  options: [],
  transcript: '2. 填空：$x^2 - 5x + 6 = 0$ 的两根之和是 ____',
  standard_answer: 'x = 2 或 x = 3',
});

/** 解答题：**只能人工确认**（答案的 reason 就是服务端 autojudge 的那句原话）。 */
export const SOLUTION = problem({
  id: 'p-sol03',
  type: 'solution',
  type_cn: '解答题',
  options: [],
  transcript: '3. 证明：两个集合相等当且仅当互相包含。',
  standard_answer: '见正解',
  correct_solution: '标准解法：先证充分性，再证必要性。',
  auto_judge: {
    eligible: false,
    reason: 'solution_type',
    reason_text: '解答题只能人工确认：过程题在屏幕上敲不出过程',
  },
  screen_redo: {
    ready: false,
    blockers: ['solution_type'],
    blocker_text: ['解答题只能人工确认：过程题在屏幕上敲不出过程'],
  },
});

/** 已审核、能自动判定，**但没有擦除手写后的题面图**：不得进屏幕重做，也不许退回原图。 */
export const NO_CLEAN = problem({
  id: 'p-noclean04',
  transcript: '4. 这道题没有擦除图。',
  images: {
    original: '/api/problem/p-noclean04/image/original',
    clean: null,
    mask: null,
  },
  has_clean: false,
  auto_judge: {eligible: true, reason: null, reason_text: null},
  screen_redo: {
    ready: false,
    blockers: ['no_clean_image'],
    blocker_text: ['缺少擦除手写后的题面图 → 不能进屏幕重做（也不许退回原图：原图印着订正）'],
  },
});

/** 还没审核：标准答案还不可信，不参与自动判定。 */
export const UNREVIEWED = problem({
  id: 'p-unrev05',
  type: 'fillin',
  options: [],
  review: 'unreviewed',
  transcript: '5. 还没审核的题。',
  auto_judge: {
    eligible: false,
    reason: 'unreviewed',
    reason_text: '这道题还没审核 → 标准答案还不可信，不参与自动判定',
  },
  screen_redo: {
    ready: false,
    blockers: ['unreviewed'],
    blocker_text: ['这道题还没审核 → 标准答案还不可信，不参与自动判定'],
  },
});

export const ALL = [CHOICE, FILLIN, SOLUTION, NO_CLEAN, UNREVIEWED];

/**
 * 一份形状与 `GET /api/index` 的 `data` 一致的索引（契约 §3）。
 * `screen_redo` 是**服务算好的读数**，照 §6.1 的形状抄；自检只读它，不重算。
 */
export const INDEX = {
  built_at: '2026-10-04T09:00:00+00:00',
  count: ALL.length,
  problems: ALL,
  stats: {
    problems: ALL.length,
    in_default_list: 3,
    cooling: 0,
    graduated: 0,
    auto_judge_eligible: 3,
    auto_judge_ineligible: 2,
  },
  screen_redo: {
    default_basis: 'in_default_list',
    bases: {
      in_default_list: {
        basis_text: '默认打印清单（未毕业且已脱离冷却）',
        ready: 2,
        blocked_total: 3,
        not_auto_judgeable: {
          count: 2,
          by_reason: [
            {reason: 'solution_type', reason_text: '解答题只能人工确认：过程题在屏幕上敲不出过程',
              count: 1, ids: ['p-sol03']},
            {reason: 'unreviewed', reason_text: '这道题还没审核 → 标准答案还不可信，不参与自动判定',
              count: 1, ids: ['p-unrev05']},
          ],
        },
        no_clean_image: {
          count: 1,
          ids: ['p-noclean04'],
          message: '另有 1 道因缺少擦除手写后的题面图不能进屏幕重做',
        },
      },
      including_cooling: {
        basis_text: '未毕业（含冷却中，「显示冷却中的题」勾上时）',
        ready: 2,
        blocked_total: 3,
        not_auto_judgeable: {count: 2, by_reason: []},
        no_clean_image: {count: 1, ids: ['p-noclean04'],
          message: '另有 1 道因缺少擦除手写后的题面图不能进屏幕重做'},
      },
    },
  },
  warnings: [],
};
