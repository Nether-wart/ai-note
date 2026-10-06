import React from 'react';
import katex from 'katex';
import Mermaid from '@theme/Mermaid';
import {mathKind, mathParts, segments} from '../lib/richtext';
import 'katex/dist/katex.min.css';

/**
 * 题面／原解／正解那类**带公式的正文**的渲染。
 *
 * 两件事，一层里做完：
 *
 *   · **```mermaid 围栏**交给主题自己的 `@theme/Mermaid`（`docusaurus.config.js` 里
 *     装了 `@docusaurus/theme-mermaid`）。围栏语言写别名（`mmd`）也认——
 *     模型偶尔会那么写，而那是同一件事。
 *   · 其余部分里的 **`$…$`（行内）与 `$$…$$`（独立成行）**交给 KaTeX。
 *     数学一律 LaTeX：`CONTEXT.md` 的「题面转录」写着数学用 LaTeX，而它**不用于印刷**
 *     ——这里只负责读起来像公式，而不是让人对着 `$\exists x \in [1,4]$` 自己脑补。
 *
 * 三条不许：
 *
 *   1. **不吞错**：KaTeX 抛了或渲染不出来，就把原样的 `$…$` 印出来（`throwOnError: false`
 *      再加一层 try）——宁愿让人看见源码，也不能让那一段凭空消失。
 *   2. **不改写原文**：这里只做渲染，不在文本上做任何增删。
 *   3. **不引入第二套 Markdown 渲染器**：只用这两个自带的能力，别的语法原样显示。
 *
 * 切分规则住在 `lib/richtext.js`（纯逻辑、可脱网测）；这一层只管渲染。
 */
export default function RichText({text, className = null}) {
  const value = typeof text === 'string' ? text : '';
  if (!value) {
    return null;
  }
  return <span className={className}>{segments(value).map(renderSegment)}</span>;
}

function renderSegment(segment, index) {
  if (segment.kind === 'mermaid') {
    return <Mermaid key={`mermaid-${index}`} value={segment.value.trim()} />;
  }
  return mathParts(segment.value).map((part, partIndex) => {
    const key = `${index}-${partIndex}`;
    const math = mathKind(part);
    if (!math) {
      return <React.Fragment key={`text-${key}`}>{part}</React.Fragment>;
    }
    let html;
    try {
      html = katex.renderToString(math.tex, {throwOnError: false, displayMode: math.display});
    } catch (error) {
      // 渲染不出来就**原样印出来**：让人看见源码，好过那一段凭空消失。
      return <code key={`raw-${key}`}>{part}</code>;
    }
    return (
      <span
        key={`math-${key}`}
        data-math={math.display ? 'block' : 'inline'}
        dangerouslySetInnerHTML={{__html: html}}
      />
    );
  });
}
