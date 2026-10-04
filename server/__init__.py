"""错题本后端：只读 HTTP 层（契约 v0，见 docs/contracts/http-api-v0.md）。

契约优先（ADR 0007 第 1 条）：`server/` 从零写，不演化、不 import `proto/`。
`proto/` 是冻结的只读证据——提示词、阈值、统计口径照抄，代码不照抄。
"""
