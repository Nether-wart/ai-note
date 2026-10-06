/**
 * 索引 → 左侧栏那棵树 + 各栏的取数（纯逻辑，一行 React 都没有）。
 *
 * 信息架构（ADR 0009、issue #17）：
 *
 *   首页总览 → 科目 →〔简报｜细则｜考点大纲｜今日重做〕，外加「未归类」兜底。
 *
 * 四条纪律住在这里：
 *
 * 1. **科目是树的第一级**，取值来自索引的 `subjects`（受控词表）。界面不自己编一个科目。
 * 2. **一道题都不许少**。所以树的科目是**两处的并集**：词表里的（哪怕 0 道）与
 *    `stats.by_subject` 里出现过的（哪怕词表里没有——那种卡的 `subject_unknown` 已经喊过了，
 *    再把它从树上丢掉就是第二次静默）。表外科目挂 `offVocabulary: true`，界面要标出来。
 * 3. **细则按录入时间由新到老**（`CONTEXT.md` 的「细则」）。不是上次重做时间——那是
 *    默认打印清单的排法，用它会让这份明细每重做一次就重排一次。
 * 4. **排序不许把题吃掉**：没有／读不出的 `created_at` 的题排在最后并单独报出来
 *    （`undated`），绝不因为排不了序就把它丢了。
 */

// 显式 `.js` 扩展名是**必须的**，不是风格问题：这个模块要能被 `node --test` 直接跑
// （纯逻辑不许只在打包器里成立），而 Node 的 ESM 不做扩展名补全。
// 既有 `redo.js`／`split.js` 之所以没这个问题，是因为它们**没有内部 import**。
import {UNCLASSIFIED, subjectUrl} from './routes.js';
// 「读不读得出时刻」只有一条判据，与时间索引共用（`time.js` 的 `parseMoment`）。
import {parseMoment} from './time.js';

const ZERO_COUNTS = {
  problems: 0,
  in_default_list: 0,
  cooling: 0,
  graduated: 0,
  auto_judge_eligible: 0,
  unreviewed: 0,
};

export function blankCounts() {
  return {...ZERO_COUNTS};
}

/** 科目那一栏的显示名。未归类是一等状态，不是空名字。 */
export function subjectLabel(name) {
  return name || UNCLASSIFIED;
}

export function isUnclassified(name) {
  return !name;
}

/**
 * 侧栏那棵树。返回 `{subjects, unclassified, totals}`。
 *
 * `subjects[]` 的每一项：`{name, label, counts, offVocabulary, brief}`。
 * 顺序照词表的顺序（那是人定的顺序），表外科目排在后面——**但在**，不消失。
 */
export function sidebarTree(data) {
  const stats = data?.stats || {};
  const bySubject = stats.by_subject || {};
  const vocabulary = data?.subjects || [];

  const names = [...vocabulary];
  for (const name of Object.keys(bySubject)) {
    if (!names.includes(name)) {
      names.push(name);
    }
  }

  const subjects = names.map((name) => ({
    name,
    label: name,
    counts: bySubject[name] ? {...bySubject[name]} : blankCounts(),
    // 读数缺失（服务没给这个科目的桶）——不许装作 0 道，界面要把它说出来
    countsMissing: !bySubject[name],
    offVocabulary: !vocabulary.includes(name),
    brief: briefState(data, name),
  }));

  return {
    subjects,
    unclassified: {
      name: null,
      label: UNCLASSIFIED,
      count: Number(stats.unclassified || 0),
    },
    totals: {
      problems: Number(stats.problems || 0),
      inDefaultList: Number(stats.in_default_list || 0),
      cooling: Number(stats.cooling || 0),
      graduated: Number(stats.graduated || 0),
      autoJudgeEligible: Number(stats.auto_judge_eligible || 0),
      unclassified: Number(stats.unclassified || 0),
    },
  };
}

/**
 * 一个科目的题。`name` 为空 = **未归类**那一栏（`subject` 是 `null` 或缺字段的题）。
 *
 * 过滤用的是**服务给的** `problem.subject`，界面不重算归属（那会变成第二份规则）。
 */
export function problemsOfSubject(problems, name) {
  const list = problems || [];
  if (isUnclassified(name)) {
    return list.filter((p) => !p.subject);
  }
  return list.filter((p) => p.subject === name);
}

/**
 * 细则的顺序：录入时间**由新到老**。
 *
 * 比较的是**时刻**不是字符串：`created_at` 常是 `+08:00`，重做时刻是 `+00:00`，
 * 直接比字符串会把时区差当成先后（契约 §1「要比较的时刻一律先归一化到 UTC」）。
 * 排不了序的（缺字段／读不出）留在最后，并**逐个报出来**，绝不静默丢掉。
 */
export function detailOrder(problems) {
  const dated = [];
  const undated = [];
  for (const problem of problems || []) {
    const created = problem?.created_at;
    // 「读不读得出时刻」只有**一条**判据（`time.js` 的 `parseMoment`），与时间索引同一份。
    // 两处各判各的时候会出现一种题：排在表里、却进不了任何一张月卡、也不算「没有录入时间」
    // ——同一个事实的两套说法，而这个项目的失败模式正是「两个产物各说各话」。
    if (!parseMoment(created)) {
      undated.push(problem);
      continue;
    }
    // 排序仍按**时刻**：`created_at` 常是 `+08:00`、重做时刻是 `+00:00`，
    // 直接比字符串会把时区差当成先后（契约 §1：要比较的时刻一律先归一化到 UTC）。
    dated.push({problem, at: Date.parse(created)});
  }
  dated.sort((a, b) => b.at - a.at);
  return {
    ordered: [...dated.map((row) => row.problem), ...undated],
    undated,
  };
}

/**
 * 考点大纲那一栏：`{科目: {章: {节: [点]}}}` → 一串可以直接渲染的行。
 *
 * 节里没有点时，**节本身就是叶子**（种子里就是这样）——所以每节都给出
 * `points`（可能是空数组）而由界面决定怎么显示，不在这里把空数组变成别的东西。
 */
export function outlineRows(outline, name) {
  const tree = (outline || {})[name] || {};
  return Object.entries(tree).map(([chapter, sections]) => ({
    chapter,
    sections: Object.entries(sections || {}).map(([section, points]) => ({
      section,
      points: Array.isArray(points) ? points : [],
    })),
  }));
}

/** 一个科目的简报读数（服务给的那一份：`{latest_date, generated_at, stale, new_problems}`）。 */
export function briefState(data, name) {
  const briefs = data?.briefs || {};
  return briefs[name] || null;
}

/**
 * 首页总览：每个科目一行读数 + 一句「今天该做什么」。
 *
 * 那句不是在算一个新的指标，而是把**服务已经给的三样读数**摆在一起：
 * 默认清单里有多少道（`in_default_list`）、冷却中多少道、未审核多少道。
 * 界面不重新定义「今天该做什么」这个词。
 */
export function overview(data) {
  const tree = sidebarTree(data);
  const rows = tree.subjects.map((subject) => ({
    ...subject,
    today: {
      ready: subject.counts.in_default_list,
      cooling: subject.counts.cooling,
      unreviewed: subject.counts.unreviewed,
      graduated: subject.counts.graduated,
    },
  }));
  return {
    rows,
    unclassified: tree.unclassified,
    totals: tree.totals,
    // 词表本身有问题的那些读数（服务给的警告）交给页面显示，这里只把该显示的挑出来
    vocabularyWarning: (data?.warnings || []).find((w) =>
      ['subjects_vocab_missing', 'subjects_vocab_unreadable', 'subjects_vocab_empty'].includes(w.code)) || null,
  };
}

/** 一栏在侧栏里的地址（未归类那一栏 `name` 给空，URL 上就没有 name）。 */
export function viewUrlFor(name, view) {
  return subjectUrl(name, view);
}
