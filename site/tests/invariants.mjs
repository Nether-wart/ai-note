/**
 * 屏幕重做页的**界面不变量**（#8）：把「渲染结果里有什么／没有什么」写成可跑的判据。
 *
 * 三条纪律：
 *
 *   1. **不靠浏览器、不联网**：判据吃的是一个 HTML 字符串。真实渲染由
 *      `selftest.mjs` 用 React 的 `renderToStaticMarkup` 做（服务端渲染，不开浏览器），
 *      这个模块只负责判断——所以它自己没有任何依赖，`node --test` 裸跑就能验。
 *   2. **判据对着真实 HTML 写，不写 CSS 选择器**：原型的第一版自检查 `.gates`，
 *      而页面里写的是 `class="gates"`，于是 20 条全误报（`docs/acceptance-log.md:253`、
 *      `proto/server.py:983`）。这里的判据认 `<img>`／`<link>` 的真实属性写法。
 *   3. **不信声明，只信 URL**：`data-image-kind="clean"` 是组件自己贴的标签，
 *      一个把原图接上、标签忘了改的组件照样会这么说。所以判据同时看**声明**与
 *      **真实 URL**，两条各报各的（`selftest.mjs` 的坏夹具就是照这个做的）。
 *
 * 每条判据返回**违规数组**（空数组 = 这条不变量成立）。每个违规都带机器可读的 `code`，
 * 方便测试与自检断言；`details` 说清「哪儿、看到了什么」。
 */

/** 违规码表。测试与自检都对着这些码断言，别改字面量。 */
export const CODES = Object.freeze({
  questionImageNotClean: 'question_image_not_clean',
  questionImageMissing: 'question_image_missing',
  questionImageKindMismatch: 'question_image_kind_mismatch',
});

const IMAGE_URL_ATTRIBUTES = {
  img: ['src', 'srcset'],
  source: ['src', 'srcset'],
  video: ['poster'],
};

function violate(code, message, details = {}) {
  return {code, message, details};
}

// --------------------------------------------------------------- HTML 解析

const TAG_RE = /<([a-zA-Z][a-zA-Z0-9-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)\/?>/g;
const ATTR_RE = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

/** 把所有标签拆成 `{name, attrs}`。自己渲染出来的 HTML 是规整的，够用。 */
export function parseTags(html) {
  const tags = [];
  for (const match of String(html || '').matchAll(TAG_RE)) {
    const attrs = {};
    for (const attr of match[2].matchAll(ATTR_RE)) {
      const value = attr[2] ?? attr[3] ?? attr[4];
      attrs[attr[1].toLowerCase()] = value === undefined ? '' : value;
    }
    tags.push({name: match[1].toLowerCase(), attrs});
  }
  return tags;
}

/**
 * 页面上**所有**图片引用：`<img src>`、`<img srcset>`、`<link as=image href>`
 * （React 19 会给 `<img>` 额外吐一个 preload link，那也是一个引用）、`<source>`。
 *
 * 漏掉 preload 就等于漏掉一个把原图地址写进 HTML 的地方。
 */
export function imageRefs(html) {
  const refs = [];
  for (const tag of parseTags(html)) {
    const names = IMAGE_URL_ATTRIBUTES[tag.name] || [];
    for (const name of names) {
      if (tag.attrs[name]) {
        refs.push({tag: tag.name, attr: name, url: tag.attrs[name], kind: tag.attrs['data-image-kind'] ?? null});
      }
    }
    if (tag.name === 'link' && tag.attrs.as === 'image' && tag.attrs.href) {
      refs.push({tag: 'link', attr: 'href', url: tag.attrs.href, kind: tag.attrs['data-image-kind'] ?? null});
    }
  }
  return refs;
}

/** 契约 §7 的擦除图路径。题面**只能是**它。 */
export function cleanImagePath(pid) {
  return `/api/problem/${pid}/image/clean`;
}

// ------------------------------------------- 不变量 1：题面只能是擦除后的图

/**
 * 验收 1（最重要的一条，spec #1 第 3 条 + 裁决 D4）：
 *
 *   · 重做页上出现的**每一个**图片引用都必须是这张卡的**擦除手写后的题面**；
 *   · 题面被换成原图（含手写与订正）／整页照片／掩膜图 → **必须报警**；
 *   · 一张图都没有（图没渲染出来）也是坏——不能因为"没有图"就默认通过。
 *
 * 原图把答案摆在眼前，重做就没有意义了，所以这条不能只查「有没有图」。
 */
export function checkQuestionImage({html, pid, apiBase = ''}) {
  const violations = [];
  const expected = cleanImagePath(pid);
  const refs = imageRefs(html);
  const cleanRefs = refs.filter((ref) => ref.url.endsWith(expected));

  for (const ref of refs) {
    if (!ref.url.endsWith(expected)) {
      violations.push(
        violate(
          CODES.questionImageNotClean,
          `题面区出现了不是擦除图的图：<${ref.tag} ${ref.attr}="${ref.url}">。` +
            '未擦除的原图印着订正，把答案摆在做题的人面前，重做就没有意义了。',
          {found: ref.url, expected, tag: ref.tag, attr: ref.attr},
        ),
      );
    }
  }

  for (const ref of refs.filter((ref) => ref.tag === 'img')) {
    if (ref.kind !== 'clean') {
      violations.push(
        violate(
          CODES.questionImageKindMismatch,
          `<img> 的 data-image-kind 是 ${ref.kind === null ? '（没有这个属性）' : `'${ref.kind}'`}，` +
            '不是 clean。判据靠这个标记认题面图，标错了就等于没有标记。',
          {kind: ref.kind, expected_kind: 'clean', url: ref.url},
        ),
      );
    }
  }

  if (cleanRefs.length === 0) {
    violations.push(
      violate(
        CODES.questionImageMissing,
        `这道题的擦除手写后的题面图没渲染出来（HTML 里找不到 ${expected}）。` +
          '题面缺图是显式的失败，不是"没有就算了"。',
        {expected, image_refs: refs.map((ref) => ref.url)},
      ),
    );
  }

  // 兜底：这两类地址无论从哪个属性、哪个 JSON 块漏出来都算漏题。
  const apiBaseSuffix = String(apiBase || '').replace(/\/+$/, '');
  const forbidden = [
    `/api/problem/${pid}/image/original`,
    `/api/problem/${pid}/image/mask`,
    '/api/page/',
    'data/pages/',
  ];
  for (const fragment of forbidden) {
    // 已经被上面的图片引用检查抓到过的，不重复报一遍（同一个根因只出一条）
    const already = violations.some((v) => String(v.details.found || '').includes(fragment));
    if (String(html || '').includes(fragment) && !already) {
      violations.push(
        violate(CODES.questionImageNotClean, `重做页的 HTML 里出现了未擦除的素材地址：${fragment}`,
          {found: fragment, api_base: apiBaseSuffix}),
      );
    }
  }

  return violations;
}
