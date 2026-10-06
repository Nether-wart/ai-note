# `data/` —— 本机测试语料

**这不是生产数据目录。** 服务默认把数据放在**用户数据目录**
（[ADR 0008](../docs/adr/0008-runtime-files-live-in-the-user-data-dir.md)）：
Windows `%APPDATA%\ai-note`、macOS `~/Library/Application Support/ai-note`、
其它 `~/.local/share/ai-note`（`$XDG_DATA_HOME` 优先）。

这里这份是**测试语料**：一份真实数据的副本，给本机测试与试跑用。拿它起服务要显式指：

```bash
python3 -m server.app --data data --host 127.0.0.1 --port 8765
```

把这份语料搬去当生产数据（一次性）：

```bash
cp -r data/. ~/.local/share/ai-note/          # Windows 换成 %APPDATA%\ai-note
```

**只有三个文件进仓库**：`vocab/subjects.json`、`vocab/error-causes.json` 与
`vocab/topic-outline.seed.json`（词表种子）。
其余全部被 `.gitignore` 拦住——这个仓库是公开的，而这里放的是真实学生的手写作业。那几条忽略
规则现在是**安全网**：即使你显式 `--data data`，也绝不许把私人数据写进公开仓库。

| 路径 | 是什么 | 进仓库 |
|---|---|---|
| `vocab/` | 词表种子：**科目**清单、错因清单、考点大纲 | ✅ 只有这三个 |
| `problems/` | 题卡：`p-<日期>-<hash6>.json` | ❌ |
| `assets/` | 每道题的图：`-problem.png`（原图）、`-clean.png`（擦除手写后）、`-cleanmask.png`（掩膜） | ❌ |
| `pages/` | 页：整页照片 `<hash12>.png` ＋ 页文件 `<hash12>.json`（切分结果与「哪张卡在哪一块」的家） | ❌ |
| `batches/` | 打印批次 | ❌ |
| `inbox/` | 收件目录：手机上传／同步盘落进来的地方，**放一个文件就是一次录入** | ❌ |
| `index.json` | proto 留下的派生索引。新服务**不读它、不写它、不删它**（索引每次请求现算）；形状基准是 [`docs/contracts/http-api-v0.md`](../docs/contracts/http-api-v0.md)，不是这个文件 | ❌ |
| `private/` | 公开文档里被摘除的具体题目内容与订正过程（`docs/acceptance-log.md` 的全本在这里） | ❌ |

### 两份词表的形状

- `vocab/subjects.json`：`{"科目": ["数学", …]}`——**科目是导航的根**（[`CONTEXT.md`](../CONTEXT.md)），
  所以「有哪些科目」只有这一份来源。卡上的科目不在表里会被报出来（`subject_unknown`），
  不替你改；卡上没有科目是**未归类**（`subject_missing`，`hint` 级），在侧栏的「未归类」下看得见。
- `vocab/topic-outline.seed.json`：`{"大纲": {<科目>: {<章>: {<节>: [<点>, …]}}}}`——
  大纲挂在科目下，是侧栏的第三层。**这是一份种子**（只为验证受控词表机制，不是完整大纲），
  旧形状 `{"name": …, "nodes": […]}` **不再兼容**。
- 数据目录里没有这两份词表时，服务**照样起得来**，但会喊一条 `subjects_vocab_missing`：
  一声不响地返回空科目表会让整个侧栏空掉，那是这个项目最怕的静默。

## 有些目录 clone 下来是空的，不是坏了

`problems/`、`assets/`、`pages/`、`batches/`、`private/` 里的东西**本来就不进仓库**：
照片里是真实的手写与订正批注，可能带身份信息，而 **git 历史很难真正擦干净**。
所以宁可 clone 下来是空的，也不冒一次险：**种子（词表）进仓库，私人内容一律不进**。
`git status` 里出现一张学生的手写照片，比漏收一张更糟——这也是收件目录 `inbox/` 单独被拦住的原因。

## 存量数据

`pages/` 里可能只有整页照片、还没有页文件。旧的题卡可以反推成页文件：

```bash
python3 -m server.backfill --data <数据目录>            # 默认预演，只报告不写盘
python3 -m server.backfill --data <数据目录> --apply    # 真写
```
