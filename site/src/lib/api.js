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

export async function requestJson(apiBase, path) {
  const base = String(apiBase || '').replace(/\/+$/, '');
  const url = `${base}${path}`;

  let response;
  try {
    response = await fetch(url, {headers: {Accept: 'application/json'}});
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

/** 图片 URL。**只喂契约里给的 `images.*`**，界面不许自己拼路径。 */
export const assetUrl = (apiBase, path) =>
  path ? `${String(apiBase || '').replace(/\/+$/, '')}${path}` : null;
