/**
 * 地址的形状（纯逻辑，一行 React 都没有）。
 *
 * 全部走**查询串**（裁决 19b）：
 *   `/`                                     首页总览（跨科目）
 *   `/subject?name=<科目>&view=<栏>`         科目下的一栏
 *   `/subject?view=<栏>`（不给 name）        「未归类」那一栏
 *   `/problem?pid=<题卡 id>`                 阅读页（每题一页）
 *   `/redo?queue=…&i=…&basis=…`              今日重做（既有契约，一个字都不动）
 *   `/split?page=<页 id>`                    切分修正（退化成从细则进来的入口）
 *
 * 为什么是查询串而不是路径式深链：ADR 0006 那处**待验证项**写着「Docusaurus 以应用方式
 * 使用时，路由与构建产物的形态要实测（尤其 `baseUrl` 与 Tauri 加载本地文件时的相对路径）」
 * ——路径式深链在 `file://` 下最容易碎；而现有三页已经是查询串风格，统一比另立一套好。
 *
 * 解析一律**明确失败**（与 `lib/redo.js` 的 `parseRedoQuery` 同一条纪律）：
 * 缺参数、认不出的栏位都返回一个带 `code` 的错误，**绝不悄悄落到一个默认值**。
 * 「打开 /problem 没给 pid，于是显示了某一道题」是比报错坏得多的一种表现。
 */

/** 第二级那四栏。`key` 出现在 URL 里，`label` 出现在界面上——顺序就是侧栏里的顺序。 */
export const VIEWS = [
  {key: 'brief', label: '简报'},
  {key: 'detail', label: '细则'},
  {key: 'outline', label: '考点大纲'},
  {key: 'redo', label: '今日重做'},
];

export const DEFAULT_VIEW = 'brief';
export const UNCLASSIFIED = '未归类';

export function viewLabels() {
  return VIEWS.map((view) => view.key);
}

/** `?a=b` → 一个对象。参数重复时**取第一个**（重复本身就是可疑输入，别猜哪个对）。 */
export function parseSearch(search) {
  const params = new URLSearchParams(String(search || ''));
  const out = {};
  for (const [key, value] of params.entries()) {
    if (!(key in out)) {
      out[key] = value;
    }
  }
  return out;
}

export function subjectUrl(name, view = DEFAULT_VIEW, {month = null, week = null} = {}) {
  const params = new URLSearchParams();
  // 不给 `name` = 未归类那一栏。空串与缺字段在这里是**同一件事**，
  // 所以 URL 上也写成同一件事，免得出现 `?name=&view=…` 这种两可的形状。
  if (name) {
    params.set('name', name);
  }
  params.set('view', view);
  // 时间筛选（只有「细则」用得上）也跟着地址走：筛完的视图要能刷新、能分享。
  if (month) {
    params.set('month', month);
  }
  if (week) {
    params.set('week', week);
  }
  return `/subject?${params.toString()}`;
}

export function problemUrl(pid) {
  return `/problem?pid=${encodeURIComponent(pid || '')}`;
}

export function splitUrl(pageId) {
  return `/split?page=${encodeURIComponent(pageId || '')}`;
}

/**
 * `/subject` 的查询串 → `{name, view, month, week}` 或 `{error}`。
 *
 * `name` 缺省 = 未归类（那是一栏，不是错误）。`view` 缺省 = 简报（用户没说要哪一栏时
 * 给第一栏是合理的默认，与「落到某道题上」不是一回事）。
 * 认不出的 `view` **明确失败**：猜一个栏位等于把一次链接失效伪装成一次正常跳转。
 *
 * `month`／`week` 是「细则」的时间筛选，形状在这里验（`YYYY-MM` / `YYYY-MM-DD`），
 * **存不存在**由数据那一层判（`lib/time.js` 的 `filterByTime` 会明确失败）。
 * 两层各管一件：这一层管「这看起来是不是一个键」，那一层管「索引里有没有这一桶」。
 */
export function parseSubjectQuery(search) {
  const params = parseSearch(search);
  const view = params.view || DEFAULT_VIEW;
  if (!viewLabels().includes(view)) {
    return {
      error: {
        code: 'view_unknown',
        message: `认不出的栏位：${view}`,
        hint: `可取值：${viewLabels().join('、')}`,
      },
    };
  }
  const month = params.month || null;
  const week = params.week || null;
  if (month && !/^\d{4}-\d{2}$/.test(month)) {
    return {
      error: {
        code: 'month_malformed',
        message: `月份的形状不对：${month}`,
        hint: '月份写成 `YYYY-MM`（例如 2026-10）；不带这个参数就是不筛。',
      },
    };
  }
  if (week && !/^\d{4}-\d{2}-\d{2}$/.test(week)) {
    return {
      error: {
        code: 'week_malformed',
        message: `那一周的形状不对：${week}`,
        hint: '周写成那一周的**周一** `YYYY-MM-DD`；不带这个参数就是不筛。',
      },
    };
  }
  if (week && !month) {
    return {
      error: {
        code: 'week_without_month',
        message: '只给了周、没给月份',
        hint: '周是某个月里的周，两个参数要一起给（月 `YYYY-MM` ＋ 周 `YYYY-MM-DD`）。',
      },
    };
  }
  if ((month || week) && view !== 'detail') {
    // 只有「细则」按时间索引。别的栏位收到这两个参数时**明确失败**：静默忽略会让人
    // 以为「这个筛选对简报也生效」，而页面上什么都不会变。
    return {
      error: {
        code: 'time_filter_needs_detail',
        message: `时间筛选只属于「细则」，当前栏位是 ${view}`,
        hint: '把 `month`／`week` 去掉，或把 `view` 改成 `detail`。',
      },
    };
  }
  return {name: params.name || null, view, month, week};
}

/** `/problem` 的查询串 → `{pid}` 或 `{error}`。没有 pid 就明确失败，绝不落到某一道题。 */
export function parseProblemQuery(search) {
  const params = parseSearch(search);
  const pid = (params.pid || '').trim();
  if (!pid) {
    return {
      error: {
        code: 'pid_missing',
        message: '地址里没有 pid',
        // 例子里的 id **不许写成一个真的形状**（`p-<日期>-<6位十六进制>`）：
        // 它会跟着打包进产物，而 ADR 0001 的自查 `grep -rl "p-2026" build/` 必须为空
        // （那道自查是「构建产物里没有一道题的内容」唯一的机器判据）。
        hint: '阅读页的地址长这样：/problem?pid=<题卡 id>',
      },
    };
  }
  return {pid};
}
