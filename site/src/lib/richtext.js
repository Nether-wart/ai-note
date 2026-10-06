/**
 * 带公式的正文怎么切（纯逻辑，一行 React 都没有——组件那一半在
 * `components/RichText.js`，KaTeX/Mermaid 的渲染在那边）。
 *
 * 放在这里是为了能测：`RichText.js` 要 import `katex` 与 `@theme/Mermaid`，
 * 而 `@theme/*` 是打包器才认的别名，`node --test` 起不来。
 * 切分规则住在这里，渲染住在那一边——两件事各自可以被单独验。
 */

/** 先按 ```mermaid／```mmd 围栏切开：围栏里的原文交给 Mermaid，其余段落再按公式切。 */
export function segments(value) {
  const text = typeof value === 'string' ? value : '';
  const out = [];
  const fence = /```(?:mermaid|mmd)[ \t]*\r?\n([\s\S]*?)```/g;
  let cursor = 0;
  let match;
  while ((match = fence.exec(text)) !== null) {
    if (match.index > cursor) {
      out.push({kind: 'text', value: text.slice(cursor, match.index)});
    }
    out.push({kind: 'mermaid', value: match[1]});
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    out.push({kind: 'text', value: text.slice(cursor)});
  }
  return out.length > 0 ? out : [{kind: 'text', value: text}];
}

/**
 * 一段纯文本 → 「原样」与「公式」交替的片段。**分隔符留在片段里**，
 * 所以判种类只看片段自己（不以 `$` 结尾的残片就是普通文字）。
 *
 * 行内 `$…$` 不跨行（`[^$\n]`）：转录里一个落单的 `$` 很常见，
 * 让它跨行去凑一对会把中间整段正文吞成公式。
 */
export function mathParts(value) {
  const text = typeof value === 'string' ? value : '';
  return text.split(/(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$)/g).filter((part) => part !== '');
}

/** 一个片段是不是公式、是行内还是独立成行。 */
export function mathKind(part) {
  if (part.startsWith('$$') && part.endsWith('$$') && part.length > 4) {
    return {display: true, tex: part.slice(2, -2)};
  }
  if (part.startsWith('$') && part.endsWith('$') && part.length > 2) {
    return {display: false, tex: part.slice(1, -1)};
  }
  return null;
}
