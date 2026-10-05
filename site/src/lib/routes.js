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

export function subjectUrl(name, view = DEFAULT_VIEW) {
  const params = new URLSearchParams();
  // 不给 `name` = 未归类那一栏。空串与缺字段在这里是**同一件事**，
  // 所以 URL 上也写成同一件事，免得出现 `?name=&view=…` 这种两可的形状。
  if (name) {
    params.set('name', name);
  }
  params.set('view', view);
  return `/subject?${params.toString()}`;
}

export function problemUrl(pid) {
  return `/problem?pid=${encodeURIComponent(pid || '')}`;
}

export function splitUrl(pageId) {
  return `/split?page=${encodeURIComponent(pageId || '')}`;
}

/**
 * `/subject` 的查询串 → `{name, view}` 或 `{error}`。
 *
 * `name` 缺省 = 未归类（那是一栏，不是错误）。`view` 缺省 = 简报（用户没说要哪一栏时
 * 给第一栏是合理的默认，与「落到某道题上」不是一回事）。
 * 认不出的 `view` **明确失败**：猜一个栏位等于把一次链接失效伪装成一次正常跳转。
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
  return {name: params.name || null, view};
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
        hint: '阅读页的地址长这样：/problem?pid=p-20261004-41c86b',
      },
    };
  }
  return {pid};
}
