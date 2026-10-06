"""错题本后端（Python）：数据读写、派生索引、模型调用、状态机、审计；不负责渲染页面（ADR 0007）。

只读 HTTP 层实现契约 v0，见 `docs/contracts/http-api-v0.md`。

契约优先（ADR 0007 第 1 条）：`server/` 从零写，不演化、不 import `proto/`。
`proto/` 是冻结的只读证据——提示词、阈值、统计口径照抄，代码不照抄。
"""
