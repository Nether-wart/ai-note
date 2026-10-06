// 界面壳：Docusaurus 站点（ADR 0006）。
//
// 两条架构约束写在配置里而不是文档里：
//   1. **数据不进构建产物**（ADR 0001/0006）：索引在**运行时**从只读服务取，
//      所以这里的 apiBase 是一个浏览器用得上的地址，不是构建期的读取。
//   2. **Tauri 兼容**：产物是纯静态 bundle、不做 SSR。打包成 Tauri 时把
//      `AI_NOTE_BASE_URL=./` 传进来即可（本地文件加载要相对路径）。
const apiBase = process.env.AI_NOTE_API || 'http://127.0.0.1:8765';

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: '错题本',
  tagline: '录入 → 重做 → 直到掌握',
  url: process.env.AI_NOTE_SITE_URL || 'http://127.0.0.1:3000',
  baseUrl: process.env.AI_NOTE_BASE_URL || '/',
  organizationName: 'nether-wart',
  projectName: 'ai-note',
  trailingSlash: true,
  onBrokenLinks: 'throw',
  i18n: {defaultLocale: 'zh-Hans', locales: ['zh-Hans']},
  // 界面从哪取数据。**运行时**读它（见 src/lib/api.js），不参与构建。
  customFields: {apiBase},
  // 图：错因与推理偶尔画一张图，`mermaid: true` 让 ```mermaid 围栏可用
  //（渲染由 `@theme/Mermaid` 做，见 `components/RichText.js`）。
  // **数学不在这里**：这个站点的页面是 `.js` 而不是 MDX，`remark-math`／`rehype-katex`
  // 那种「构建期把 markdown 里的公式转成 HTML」的路子对本项目**不生效**——
  // 公式是在运行时由 `RichText` 用 `katex.renderToString` 渲染的，
  // 所以这里只装主题、不装那对插件（KaTeX 的样式也在那个组件里 import 本地那份，
  // 不引远端 stylesheet：这个站点要在没网的本机上跑）。
  themes: ['@docusaurus/theme-mermaid'],
  markdown: {mermaid: true},
  presets: [
    [
      'classic',
      {
        docs: false,
        blog: false,
        theme: {customCss: './src/css/custom.css'},
      },
    ],
  ],
  themeConfig: {
    navbar: {
      title: '错题本',
      items: [
        // 首页是**跨科目总览**（#17 §0）：错题住在侧栏的「科目 → 细则」里，不在导航栏
        {to: '/', label: '总览', position: 'left'},
        // 「怎么用」是这里唯一的 **MDX 页面**：它没有数据，所以能长在 Docusaurus 的框架上
        // （markdown 排版、admonition、mermaid 都是主题给的），而数据页必须留在运行时。
        {to: '/help', label: '怎么用', position: 'right'},
      ],
    },
  },
};

module.exports = config;
