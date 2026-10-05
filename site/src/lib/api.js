/**
 * 数据访问层：界面与后端之间**唯一**的接缝。
 *
 * 契约：`docs/contracts/http-api-v0.md`。两条纪律：
 *   1. 数据**在运行时**取（ADR 0001：数据变更绝不触发 Docusaurus 重建），
 *      所以这里没有一处构建期读取；
 *   2. **不许静默**——服务给的 `warnings[]` 与 `skipped[]` 一路带到界面上，
 *      出错时把服务返回的原话 (`error.message`) 和 `hint` 一起显示，
 *      绝不用 `catch {}` 吞掉。
 */

export class ApiError extends Error {
  constructor(error, status, url) {
    super(error?.message || `请求失败（HTTP ${status}）：${url}`);
    this.name = 'ApiError';
    this.code = error?.code || 'unknown_error';
    this.reason = error?.reason || this.code;
    this.hint = error?.hint;
    this.details = error?.details || {};
    this.status = status;
    this.url = url;
  }
}

export async function requestJson(apiBase, path, {method = 'GET', body = null, form = null} = {}) {
  const base = String(apiBase || '').replace(/\/+$/, '');
  const url = `${base}${path}`;

  const init = {method, headers: {Accept: 'application/json'}};
  if (form !== null) {
    // multipart：**不要**自己设 `Content-Type`——boundary 是浏览器生成的，
    // 手写一个就等于亲手把这条请求弄坏（而症状是服务端说「不是 multipart」，很容易查错方向）。
    init.body = form;
  } else if (body !== null) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, init);
  } catch (cause) {
    // 连不上服务是最常见的一种失败，它必须说清「该去起什么」。
    throw new ApiError(
      {
        code: 'network_error',
        message: `连不上服务：${url}`,
        hint: '服务起了吗？在仓库根跑：python3 -m server.app --port 8765',
      },
      0,
      url,
    );
  }

  let envelope = null;
  try {
    envelope = await response.json();
  } catch (cause) {
    envelope = null;
  }
  if (!envelope || typeof envelope !== 'object') {
    throw new ApiError(
      {
        code: 'not_json',
        message: `服务没有返回 JSON（HTTP ${response.status}）：${url}`,
        hint: '契约要求每个响应都是同一个信封；拿到别的东西说明请求没打到服务上',
      },
      response.status,
      url,
    );
  }
  if (envelope.ok === false || !response.ok) {
    throw new ApiError(envelope.error, response.status, url);
  }
  // 整个信封都返回：调用方要 `data`，也要 `warnings` / `skipped`。
  return envelope;
}

export const fetchIndex = (apiBase) => requestJson(apiBase, '/api/index');

export const fetchProblem = (apiBase, pid) =>
  requestJson(apiBase, `/api/problem/${encodeURIComponent(pid)}`);

/**
 * 写端点：提交一次屏幕重做的作答（契约 §10.1，归属 #5）。
 *
 * 界面**只提交「我答的是什么」**（`redo.screenPayload`）：`verdict`／`source`／
 * `confidence`／`provider`／`model` 一个都不许由客户端给——判定是服务的事。
 * 拒绝（422 不能自动判定／502 模型没问成）也走同一个信封，抛 `ApiError`，
 * 调用方照 `error.message` 原话显示。
 */
export const postAttempt = (apiBase, pid, payload) =>
  requestJson(apiBase, `/api/attempt/${encodeURIComponent(pid)}`, {method: 'POST', body: payload});

/** 图片 URL。**只喂契约里给的 `images.*`**，界面不许自己拼路径。 */
export const assetUrl = (apiBase, path) =>
  path ? `${String(apiBase || '').replace(/\/+$/, '')}${path}` : null;

/**
 * 页资源（契约 §10.2，工单 #14）。四个动作一个接缝：
 * **模型调用与图像统计都在服务内部，界面不碰照片处理**。
 *
 * 界面只提交「人做了什么」（`edits` 里的动作与块的 id／边界／题号／题型／收不收）；
 * 判定字段与取舍规则一律由服务给（#12）。`dry_run` 是预演，服务一个字节都不写。
 */
export const fetchPage = (apiBase, pageId) =>
  requestJson(apiBase, `/api/page/${encodeURIComponent(pageId)}`);

export const patchPage = (apiBase, pageId, payload) =>
  requestJson(apiBase, `/api/page/${encodeURIComponent(pageId)}`, {method: 'PATCH', body: payload});

/**
 * **读一页**（打开一页来改时的第一次读取）。
 *
 * 走 `GET /api/page/<id>`——契约 §10.2.1b 的动作表里那条**读**。
 *
 * 这里有一段弯路值得留着当教训：起先契约与实现**都没有**这条路由，于是「读一页」只能
 * 拿一次**空修正的预演**（`PATCH {dry_run: true, edits: []}`）去凑——让**读**依赖一条
 * **写形状**的路由。而真正的问题是它**在真 socket 上是 405**：`SplitEditor` 的
 * `fetchPage` 从 #14 起就踩在这条死缝上，可它的测试全靠注入 `pageData`，所以**从没红过**
 * ——判据的层级与坏掉的那一层错开一格，这是这一类缺口能活下来的唯一原因。
 * 现在读与写分开，这里只发一个纯 GET。
 */
export async function readPage(apiBase, pageId) {
  const envelope = await fetchPage(apiBase, pageId);
  const page = envelope?.data?.page;
  if (!page) {
    throw new ApiError(
      {
        code: 'page_missing_in_envelope',
        message: `读这一页的回执里没有 page：${pageId}`,
        hint: '按契约，读一页的回执带整份 page；没有就是端点形状变了，照服务的原话查。',
      },
      200,
      `/api/page/${encodeURIComponent(pageId)}`,
    );
  }
  return {envelope, page};
}

/**
 * 「重置为预设」：**破坏性**，会丢掉人工增删改的块（契约 §10.2.1b，本版语义改动）。
 *
 * `confirmDiscardManual: false`（缺省）时带一个**空** body 先试一次：这一页若存在任何
 * 人工改动，服务会给 **409** `resegment_needs_confirmation`，`details.discarded` 报清
 * 「将丢弃几处人工改动」。界面把那份读数**原话**摆出来**再问一次**，用户点头之后才
 * 带 `{"confirm_discard_manual": true}` 重发。**不信**界面自己估的数字——那个口径只有在
 * 服务那一处实现（`page_edit` 的分派表旁边）。
 */
export function resegmentPage(apiBase, pageId, {confirmDiscardManual = false} = {}) {
  return requestJson(apiBase, `/api/page/${encodeURIComponent(pageId)}/resegment`, {
    method: 'POST',
    body: confirmDiscardManual ? {confirm_discard_manual: true} : {},
  });
}

/**
 * 页的整页照片 URL。页文件与照片**同目录并列**（D5），照片经图片端点取。
 * 界面**不许自己拼盘上路径**——这里只拼这一个已知形状的服务地址。
 */
export const pageImageUrl = (apiBase, pageId) =>
  `${String(apiBase || '').replace(/\/+$/, '')}/api/page/${encodeURIComponent(pageId)}/image`;

/**
 * 「建」：一张照片 → 一页（契约 §10.2.1b）。`file` 是**文件**字段，
 * `subject` 是**文本**字段——科目在录入时由人指定（`CONTEXT.md`），不是让机器猜的。
 *
 * 科目不在受控词表里 → **400**，`error.message` 与 `hint` 是服务的原话，照原话显示；
 * 不给科目是允许的（那就是**未归类**，一等状态）。
 *
 * 切分不可用时这条请求**照样建页**（契约 §10.2.1）：`blocks` 是 `null`、照片在盘上，
 * 界面要据此开放手动画框——那不是错误，是一个要人接着做完的状态。
 */
export function postPage(apiBase, {file, filename = 'page.png', subject = null}) {
  const form = new FormData();
  form.append('file', file, filename);
  if (subject) {
    form.append('subject', subject);
  }
  return requestJson(apiBase, '/api/page', {method: 'POST', form});
}

/** 「入库」：把「收」的块变成骨架卡（契约 §10.2.1b）。幂等，重复提交不会换 id。 */
export const commitPage = (apiBase, pageId) =>
  requestJson(apiBase, `/api/page/${encodeURIComponent(pageId)}/commit`, {method: 'POST'});

/**
 * 简报（契约 §10.5）。`at` 给一个 `YYYY-MM-DD` 就是取历史上那一天那一份；不给就是最新一份。
 * 没有简报时服务给 **404 `brief_missing`**——那是「还没有」，不是「读不到」，界面要分开说。
 */
export function fetchBrief(apiBase, subject, at = null) {
  const query = at ? `?at=${encodeURIComponent(at)}` : '';
  return requestJson(apiBase, `/api/brief/${encodeURIComponent(subject)}${query}`);
}

/**
 * 生成一份简报：**会问模型**，因此慢、也可能失败（502）。
 * `window_days` 缺省 7；`history_facts` 那一半数的永远是全部历史。
 *
 * 一份数字对不上的简报服务**不落盘**、直接 502 `brief_unverifiable`——那是「模型编了数字」，
 * 与「模型没问成」是两件事，界面照 `error.message` 原话显示，不许自己编一句委婉说法。
 */
export const postBrief = (apiBase, subject, windowDays = null) =>
  requestJson(apiBase, `/api/brief/${encodeURIComponent(subject)}`, {
    method: 'POST',
    body: windowDays ? {window_days: windowDays} : {},
  });
