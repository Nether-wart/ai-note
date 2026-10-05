# `data/` —— 你的资料

**只有两个文件进仓库**：`vocab/error-causes.json` 与 `vocab/topic-outline.seed.json`（词表种子）。
其余全部被 `.gitignore` 拦住——这个仓库是公开的，而这里放的是真实学生的手写作业。

| 路径 | 是什么 | 进仓库 |
|---|---|---|
| `vocab/` | 词表种子：错因清单、知识点大纲 | ✅ 只有这两个 |
| `problems/` | 题卡：`p-<日期>-<hash6>.json` | ❌ |
| `assets/` | 每道题的图：`-problem.png`（原图）、`-clean.png`（擦除手写后）、`-cleanmask.png`（掩膜） | ❌ |
| `pages/` | 页：整页照片 `<hash12>.png` ＋ 页文件 `<hash12>.json`（切分结果与「哪张卡在哪一块」的家） | ❌ |
| `batches/` | 打印批次 | ❌ |
| `inbox/` | 收件目录：手机上传／同步盘落进来的地方，**放一个文件就是一次录入** | ❌ |
| `index.json` | proto 留下的派生索引。新服务**不读它、不写它、不删它**（索引每次请求现算）；形状基准是 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)，不是这个文件 | ❌ |
| `private/` | 公开文档里被摘除的具体题目内容与订正过程（`docs/acceptance-log.md` 的全本在这里） | ❌ |

## 为什么切得这么碎

照片里是真实的手写与订正批注，可能带身份信息，而 **git 历史很难真正擦干净**。
所以宁可 clone 下来是空的，也不冒一次险：**种子（词表）进仓库，私人内容一律不进**。
`git status` 里出现一张学生的手写照片，比漏收一张更糟——这也是收件目录 `inbox/` 单独被拦住的原因。

## 可以搬到别处

整个数据目录由 `--data` / `AI_NOTE_DATA` 指定（默认就是这个目录）。
收件目录跟着 `--data` 走（`AI_NOTE_INBOX` 可单独覆盖），所以把数据指到 `~/ai-note-data` 时，
上传不会再落回仓库里的 `data/inbox/`。

## 存量数据

`pages/` 里现在可能只有整页照片、还没有页文件。旧的题卡可以反推成页文件：

```bash
python3 -m server.backfill            # 默认预演，只报告不写盘
python3 -m server.backfill --apply    # 真写
```
