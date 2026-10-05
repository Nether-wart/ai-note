# `docs/` —— 文档索引

按「要做什么 → 为什么这么定 → 界面与后端怎么对接 → 模型能不能上岗 → 还有哪些决定可以翻」的顺序读。

| 路径 | 是什么 |
|---|---|
| `specs/` | 两份规格的**指针**：[`screen-redo.md`](specs/screen-redo.md)、[`page-segmentation.md`](specs/page-segmentation.md)。正文住在 issue [#1](https://github.com/Nether-wart/ai-note/issues/1) 与 [#2](https://github.com/Nether-wart/ai-note/issues/2) 里，这里不留副本——两份副本会漂移 |
| `adr/` | 7 篇架构决定（0001–0007），按编号读。**动手改行为之前先看有没有 ADR 拦着** |
| `contracts/http-api-v0.md` | 界面与后端之间**唯一必须跨过去的东西**（[ADR 0007](adr/0007-redesign-the-backend-contract-first.md)）：端点、错误信封、字段形状。**改行为之前先改这份文档** |
| `acceptance-log.md` | 模型上岗验收记录（公开版）。规则是每个模型在每个角色上岗前都要过视觉探针与该角色的通过标准，换模型就要重跑 |
| `decision-review.md` | 第 1 轮面谈的全部决定清单，逐条标了反转成本与信心——**这份清单是给你推翻用的** |
| `agents/` | 给 AI 助手的约定：issue 追踪（[`issue-tracker.md`](agents/issue-tracker.md)）、triage 标签、领域文档布局（[`domain.md`](agents/domain.md)）。根目录的 [`AGENTS.md`](../AGENTS.md) 指向这里 |

## 几条贯穿全仓库的规矩

- **术语以 [`CONTEXT.md`](../CONTEXT.md) 为准**：写文档、写 commit、起测试名都用那套词，
  别用术语表 `_Avoid_` 里的近义词。
- **不许静默**（[ADR 0007](adr/0007-redesign-the-backend-contract-first.md)）：拒绝要带原因码与结构化信封，
  跳过要报数报理由，缺东西要显示「缺了什么」而不是印一个空位。
- **数据不进构建产物**（[ADR 0001](adr/0001-file-backed-data-with-write-service.md)）：
  界面运行时经 HTTP 取数据，所以公开仓库的静态站点里没有一道题。
