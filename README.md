# AI 错题本

> 单用户的错题资料库：把做错的题录入、归因、打标，通过「纸上重做」反复重做，直到掌握。数据在本机，只有模型调用离开本机，且限境内服务。

**状态：`proto/` 是原型的证据（已冻结、只读）；`server/` + `site/` 是按契约重建的实现。**

- **原型**（`proto/`）：核心闭环「拍照 → 抽取 → 审核 → 打印 → 纸上重做 → 标记 → 掌握」已实现并跑通过。
  它冻结为只读参考（ADR 0007）：提示词、阈值、统计口径与实测结论被继承，**代码不被继承**。
- **重建**（`server/` + `site/`）：契约优先的后端 + 界面壳，交付了**屏幕重做**、**自动判定**、页与切分修正、
  收入决策与审计、手机上传页。它**还没做**的事写在下面「新实现」那一节里，不藏着。

本文档区分**设计意图**（记录在 `CONTEXT.md` 与 `docs/adr/`）与**当前实现**（两个目录各是一半）。

## 实现状态

逐项列出设计来源与实现状态。**未实现的东西不写进「功能特性」。**

| 能力 | 设计来源 | 实现状态 |
|---|---|---|
| 单题抽取（照片 → 题卡） | `CONTEXT.md`「原题」「题面转录」 | 已实现：`slice.py extract` |
| 裁剪框体检 | ADR 0005「实测后的修订」 | 已实现：`slice.py cropcheck` |
| 手写擦除——彩笔 | ADR 0005 决定 2 | 已实现：颜色分离（饱和度阈值 ≥60） |
| 手写擦除——黑笔 | ADR 0005 决定 2 | 已实现：模型给框 + 人工增删掩膜（半自动） |
| 审核界面 | `CONTEXT.md`「审核」 | 已实现：清单页 `/` |
| 活页纸生成与打印 | `CONTEXT.md`「重做纸」「版面格」 | 已实现：浏览器打印 HTML（非 PDF） |
| 页锚点与打印批次 | `CONTEXT.md`「页锚点」「打印批次」 | 已实现 |
| 纸上重做的判定回写 | `CONTEXT.md`「判定」 | 已实现：标记页 `/mark` |
| 掌握与冷却状态机 | `CONTEXT.md`「掌握」「冷却」 | 已实现，有独立验收 `test_mastery.py` |
| 落盘数据体检与定点修复 | ADR 0007「不许静默」 | 已实现：`--audit` / `--repair` |
| **屏幕重做** | [`docs/specs/screen-redo.md`](docs/specs/screen-redo.md)（正文在 issue #1） | 已实现：`site/` 重做页 + `POST /api/attempt/<pid>` |
| **自动判定** | `CONTEXT.md`「自动判定」 | 已实现：`server/judge.py` + 判定角色；低置信度一律落「看不清」 |
| **整页切分（一页多题）** | [`docs/specs/page-segmentation.md`](docs/specs/page-segmentation.md)（正文在 issue #2） | **部分**：候选块解析、三条对账判据与重切三态已落地（`server/segmentation.py`），**真去问模型的那一层没接**（`segmenter` 注入点是空的） |
| **复习纸** | `CONTEXT.md`「复习纸」 | **未实现** |
| **手机上传页** | ADR 0007 决定 4 | 已实现：`GET /upload` + `POST /api/inbox`（**未在真手机上验过**） |
| **收件目录监视** | ADR 0007 决定 4 | **未实现监视**（`inotify` 在同步盘上不可靠）；手动等价入口已实现：`POST /api/inbox/scan` |
| **生产前端（Docusaurus 站点）** | ADR 0006、ADR 0009 | 已实现：清单页 / 重做页 / 切分修正页（`site/`）；**重构中**：索引栏以**科目**为根（[issue #17](https://github.com/Nether-wart/ai-note/issues/17)） |
| **科目与受控词表** | `CONTEXT.md`「科目」、ADR 0009 | 已实现：`subject` 是题卡一等字段、`vocab/subjects.json` 只有一份来源、`stats.by_subject` 按科目汇总、**未归类**兜底、`python3 -m server.subject_assign --map … [--apply]` 显式回填 |
| **Tauri 打包** | ADR 0006 | **未实现** |
| **后端重写（`server/`）** | ADR 0007 | 已实现：契约 v0（[`docs/contracts/http-api-v0.md`](docs/contracts/http-api-v0.md)）+ 只读端点 + 写端点 |
| **界面鉴权** | ADR 0003（单用户本机） | 按设计不做 |
| **多用户 / 邀请 / 配额** | ADR 0002 → 作废于 ADR 0003 | 按设计不做 |

`proto/` 的定位见 ADR 0007：**一次性原型，已冻结为只读参考**。它的代码不被继承，提示词、阈值、统计口径与实测结论被继承。

## 新实现：`server/` + `site/`（契约 v0）

后端从零重建，界面是 Docusaurus 静态站，两者之间只认契约
（[`docs/contracts/http-api-v0.md`](docs/contracts/http-api-v0.md)，[ADR 0007](docs/adr/0007-redesign-the-backend-contract-first.md)）。
`server/` 是**零第三方依赖**的（只用标准库），与 `proto/` 互不 import。

```bash
# 后端：默认只监听本机。数据默认在**用户数据目录**（ADR 0008），不是这个仓库
python3 -m server.app --host 127.0.0.1 --port 8765

# 界面（另开一个终端）
cd site
npm_config_cache="$PWD/../.npm-cache" npm ci
AI_NOTE_API=http://127.0.0.1:8765 npm run start -- --port 3000 --host 127.0.0.1

# 测试：后端零依赖直接跑；界面是纯逻辑 + 真渲染出来的 HTML 的不变量自检
python3 -m pytest -q
node --test site/tests/ && node site/tests/selftest.mjs
```

**人工验收清单**（起服务、点完三个页面、哪些行为算红线）在 [`docs/walkthrough.md`](docs/walkthrough.md)。

这一层的边界，一并说清：

- **一页多题的模型切分没接上**：建页与重切都会明确报「切分不可用」，`blocks` 是 `null` 而不是 `[]`
  ——**不许返回一个看起来能跑的假块列表**。
- **纸上重做的活页纸与标记页仍是 `proto/server.py` 的**：新后端只落了屏幕重做的写端点
  （`channel:"paper"` 仍是 400），审核字段的编辑也还在原型界面上。
- **界面侧还缺「建 / 入库」两个动作的入口**（服务层已实现，界面没接）。
- 各目录的读法见 [`server/README.md`](server/README.md)、[`site/README.md`](site/README.md)、
  [`docs/README.md`](docs/README.md)、[`data/README.md`](data/README.md)。

## 这是什么 / 不是什么

**是**：一个本地优先的错题管理工具。数据以「一题一文件」存在**本机**（ADR 0001）——默认在用户数据目录（ADR 0008），仓库里那份只是测试语料；写入只经过一个独立本地服务；模型调用限境内白名单（DashScope、DeepSeek）。

**不是**：
- 不是要注册账号的产品；
- 不是把数据传到别人服务器的服务；
- **不是拍照自动批改 App**——纸上重做的判定一律由人给出（`CONTEXT.md`「判定」），这是设计，不是没做完；
- 不是可以暴露到公网的服务（ADR 0003 明确单用户本机）。

## 核心概念

术语在 `CONTEXT.md` 里都有精确定义，用错一个词往往意味着用错一个功能。以下几条最容易被误用：

| 术语 | 定义（摘自 `CONTEXT.md`） |
|---|---|
| **重做** | 对一道已录入的错题重新作答的一次事件。纸上重做与屏幕重做都是正式的重做。 |
| **判定** | 一次重做产生的结论，取值为 **对 / 错 / 看不清**，连同来源与置信度。 |
| **看不清** | 读不清内容，或系统对自己没把握。既不清零也不计入掌握。**系统的低置信度一律落向这里，绝不落向「对」。** |
| **原答** | 学生在纸上**自己原本**给出的最终答案，不含任何订正。看不出来时必须留空，绝不猜；低置信度由系统**强制留空**（阈值 0.6）。 |
| **订正** | 原答之后补上的正确答案或批注，常是红笔。**不是原答，也不等于 AI 的正解**；误认会制造出「这道题答对了」的假象。 |
| **正解** | 这道题的完整正确解答，由 AI 现解、经你确认后才成立。 |
| **标准答案** | 从正解中抽出、经你确认的最终答案。是自动判定的唯一基准。 |
| **掌握** | 连续 2 次被判定为对、且两次重做之间至少间隔 7 天。 |
| **毕业** | 达到掌握后退出默认打印清单。毕业不等于删除；任何一次判错立刻回池、毕业取消。 |
| **冷却** | 距上次重做不满 7 天。不进默认打印清单，此时的重做不推进掌握——**但判错永远立刻清零**（不对称）。 |
| **页锚点** | 重做纸每页一个的快捷入口标记，**指向一个打印批次**（不是当前排版的哈希）。 |

完整术语表（含「串题」「红笔痕迹」「手写掩膜」「受控词表」等）见 [`CONTEXT.md`](CONTEXT.md)。

## 环境与依赖

- Python 3.9+（代码用到 `from __future__ import annotations`、`datetime.fromisoformat`、`http.server.ThreadingHTTPServer`；3.7/3.8 已 EOL，未在其上验证）
- 运行时依赖：`requests`、`pillow`、`numpy`。**未固定版本、无依赖锁定文件**（`requirements.txt` 不存在，`pyproject.toml` 也不存在）——这是已知缺口。
- 新后端（`server/`）**零第三方依赖**（只用标准库）；上面三个依赖是 `proto/` 的。`site/` 的依赖由 `site/package.json` 与提交的 lock 文件管住。
- 至少一个境内模型服务的 API Key：DeepSeek 或 DashScope。

```bash
git clone https://github.com/Nether-wart/ai-note.git
cd ai-note
python3 -m venv .venv && source .venv/bin/activate
pip install requests pillow numpy
```

## 配置

```bash
cp .env.local.example .env.local
```

编辑 `.env.local`，至少填一个：

```
DEEPSEEK_API_KEY=sk-xxxx
# DASHSCOPE_API_KEY=sk-xxxx
```

可选覆盖模型角色（不填则用 `slice.py` 里的 `ROLE_DEFAULTS`）：

```
EXTRACT_PROVIDER=deepseek
EXTRACT_MODEL=deepseek-flash
JUDGE_PROVIDER=deepseek
JUDGE_MODEL=deepseek-flash
```

`.env.local` 已在 `.gitignore` 里。密钥文件按此顺序查找：仓库根 `.env.local` → `~/.env.local`；已存在的环境变量不覆盖。密钥值不会被打印。

**默认模型**（`slice.py` 的 `ROLE_DEFAULTS`）：

| 角色 | provider | model |
|---|---|---|
| `extract` | `deepseek` | `deepseek-flash`（DeepSeek-V4.1-Flash） |
| `judge` | `deepseek` | `deepseek-flash` |

Provider 是**白名单常量**，只有 `dashscope` 与 `deepseek` 两项；指到白名单外会**直接拒绝启动**（`role_config` 里 `die`）。白名单防的不是别人，是自己手滑——一张手写照片一旦出境就收不回来。

## 模型验收：不过考不许上岗

`CONTEXT.md`「验收」规定：每个模型在每个角色上岗前必须过两道——**视觉探针** + **该角色的通过标准**。换模型就要重跑。

### 视觉探针

```bash
python3 proto/slice.py models                 # 按角色列出 provider 与可用型号
python3 proto/slice.py probe --role both      # 探针：给一张随机六位数图
```

探针的实现：生成一张 680×260 白底图，写一个随机六位数（104 号字，旋转 3°），JPEG 质量 90，问模型「这张图里写着一个六位数。只回那个数字」。通过条件是数字出现在回复里。

它防的是一种静默失败：配置指到纯文本模型时它**不会报错**，它会忽略图片、照着提示词把题卡编出来——你会得到一张格式正确、内容全是幻觉的题卡。

### 角色通过标准

| 角色 | 通过标准 |
|---|---|
| `extract` | 真实照片上，**一半以上字段你不用改**（人判，无法自动量） |
| `judge` | 模型自出题 + 人手写固定考卷，**两份全对** |

固定考卷在 `proto/fixtures/`（`cases-radical.json`、`cases-interval.json`），由人手写——模型自出的用例有「自出自考」的偏向。

```bash
python3 proto/slice.py equiv --cases proto/fixtures/cases-radical.json
python3 proto/slice.py equiv --cases proto/fixtures/cases-interval.json
```

已有验收结果记在 [`docs/acceptance-log.md`](docs/acceptance-log.md)（十轮记录，含 judge 全对读数、擦除实测、探针可靠性对照实验）。**未经验收的模型不许上岗。**

## 用法

### 录入一道题（当前入口是单文件命令）

```bash
python3 proto/slice.py extract samples/某一页.jpg --note "照片里是第 12 题"
```

产出：

- `data/problems/<pid>.json` —— 题卡（一题一文件，ADR 0001）
- `data/assets/<pid>-problem.png` —— 题面裁剪图
- `data/pages/<hash>.jpg` —— 整页原图留底（永不裁剪母本）

题卡里 `review.status` 初始为 `unreviewed`。**审核不是录入的前置条件**（`CONTEXT.md`「审核」）：录入时不做任何强制确认。

> **注意**：题卡按照片内容哈希命名。同一张照片重跑 `extract` 会**覆盖**上一次的题卡——代码会在覆盖前打印警告（含上次是哪个模型、什么时候抽的），但不会阻止覆盖。做模型对比前，先把旧题卡另存。

### 裁剪体检

```bash
python3 proto/slice.py cropcheck                     # 查 data/problems 下全部
python3 proto/slice.py cropcheck data/problems/<pid>.json
```

它能查出四类问题：框内几乎空白、框内有彩笔手写、右边界外有成片墨迹、非解答题下边界外有成片墨迹。

**已知弱点**（`docs/acceptance-log.md` 第四轮明确记录）：黑笔手写与印刷体在灰度上同色，机器分不开。所以它**报不出**「框里混进了黑笔手写」，也判不了「框是否恰好等于题干」。它能说的只有「框小了」和「框里混进彩笔」这两件——而这两件恰好是最有害的（彩笔订正含答案，印上重做纸就毁掉了重做）。

### 擦除手写

```bash
python3 proto/slice.py clean data/problems/<pid>.json --boxes --verify --save
```

参数：

| 参数 | 作用 |
|---|---|
| `--boxes` | 让模型先标出手写位置（`HANDWRITING_SYSTEM` 提示词），再擦那些区域 |
| `--grow N` | 掩膜外扩像素数，吃掉抗锯齿边缘（默认 3） |
| `--verify` | 两侧探针：还剩手写吗、印刷体被擦坏了吗 |
| `--save` | 把 `clean_image` 与统计写回题卡 |

擦除是**半自动的**（ADR 0005 决定 3）：机器给初值——彩笔靠颜色分离，黑笔靠模型框——人可在审核界面里增删掩膜。掩膜规格（`boxes_norm` ∪ `manual.add` − `manual.drop`，一律归一化坐标）存进题卡，所以**擦除可重放**：改掩膜只重算，不重新问模型；换分辨率也不会漂。

擦除产出的客观闸门读数（**判据**，模型探针的判词只当线索）：

| 读数 | 应 | 它在防什么 |
|---|---|---|
| 新出现深色像素 | 0 | 填补出黑斑（真发生过：边缘补零的 bug） |
| 残留彩笔像素 | 0 | 没擦净 |
| 疑咬印刷体 | 0 | 被擦掉的墨迹左右两侧都还有墨迹 → 像在印刷笔画上啃了个洞 |

模型的判词只当线索：`docs/acceptance-log.md` 第五轮做过对照实验，同一张图连问三次，探针在擦除客观良性的那张上误报 1/3，在擦除客观有伤的那张上损伤探针**漏报 3/3**。

### 启动写入服务

```bash
python3 proto/server.py --port 8765
```

默认绑 `127.0.0.1:8765`。页锚点基址（`ANCHOR_BASE`）跟随 `--host` / `--port` 变。

| 路径 | 用途 |
|---|---|
| `/` | 清单页：审核字段、编辑掩膜、掌握状态、生成活页纸 |
| `/sheet` | 活页纸预览（**无页锚点**，无法回写） |
| `/sheet?batch=<批次>` | 某一叠（页锚点稳定） |
| `/mark?anchor=<页锚点>` | 按页锚点标记重做 |
| `/mark?batch=<批次>` | 按整批标记 |
| `/mark?pid=<题号>` | 按单题标记 |
| `/api/index` | 派生索引 JSON |
| `/assets/<file>` | 静态图片 |

清单页里：拖框加/减掩膜（松开鼠标即重算）、填审核字段（原答、标准答案、转录、考点、错因、审核状态）、勾选打印范围、点「生成活页纸」落一个打印批次并打开新标签页。

### 纸上重做的闭环

1. 清单页勾选 → 「生成活页纸」**落一个打印批次**（`data/batches/<id>.json`）并打开新标签页
2. 打印 → 在纸上重做
3. 打开 `/mark?anchor=<页脚的码>`，一屏里逐题标「对 / 错 / 看不清」（判错顺手记错因）
4. 回写 `attempts` 与掌握状态

判定回写**不经过模型**：纸上重做不拍照、不自动判（`CONTEXT.md`「判定」），所以这一条通道一律由人给出结论。

### 落盘数据体检

```bash
python3 proto/server.py --audit                   # 只读、可重跑、什么都不改
python3 proto/server.py --rebuild-index           # 只重建派生索引
python3 proto/server.py --repair <pid>            # 对着整页原图重抽，只修可疑字段（预览）
python3 proto/server.py --repair <pid> --apply    # 真写盘：退回未审核
```

`--audit` 检查六类：卡片自检（复用 `card_warnings`）、资产指向的文件是否还在、**题干转录逐字相同**（串题指纹）、索引与卡片是否一致、打印批次引用的题是否还在、掌握状态与重做历史是否自洽。

它存在的理由（ADR 0007「不许静默」）：**验收是对 `runs/` 里那次调用打的分，而题卡能被后来的路径改坏。** `docs/acceptance-log.md` 第十轮记录了真事故——界面把第一题的文字写进了第二题，而验收日志说这道题的转录完整。所以检查必须跑在**落盘的数据**上，且必须随时能重跑。

`--repair` 只修三个「可能来自另一道题」的字段（题干转录、标准答案、原答），**不整卡重跑**——卡上其余部分（掩膜、版面格、重做历史、考点错因）是人动过的，整卡重跑等于以「修数据」为名的第二次破坏。修复后**退回未审核**，改动记入 `provenance.repairs[]`。

### 界面不变量自检

```bash
python3 proto/server.py --selftest
```

不开服务，只跑界面不变量检查。它防的是一类特定的 bug：`<details>` 少了 `id`，`getElementById` 返回 `null`，查询退化到整篇文档、第二题的按钮绑到了第一题的处理器上、读数写进错的地方——而界面上**什么都不报错，只是不再更新**（`docs/acceptance-log.md` 第七轮的真事故）。

### 状态机验收

```bash
python3 proto/test_mastery.py
```

不联网、不问模型，假时钟钉住 12 条规则：当天判对不计入、判错永远清零、看不清两不相干、冷却天数读数、错因字符串不被拆成单字。

## 命令行速查

| 命令 | 作用 |
|---|---|
| `slice.py models` | 按角色列出 provider 与可用型号 |
| `slice.py probe --role {extract,judge,both}` | 视觉探针：验收第一关 |
| `slice.py accept --role {extract,judge,both} [problem]` | 验收：探针 + 该角色的通过标准 |
| `slice.py extract <photo> [--note ...]` | 从照片抽取一道题 |
| `slice.py judge <problem> <photo> [--source {auto,human}] [--cause ...]` | **已停用**（D2 翻案）；保留以便将来回头验证自动判定路径 |
| `slice.py equiv [problem] [--cases <file>]` | 等价性判定的可靠性；两份考卷都跑才算数 |
| `slice.py cropcheck [problem...]` | 裁剪框体检 |
| `slice.py clean <problem> [--boxes] [--grow N] [--verify] [--save]` | 擦除手写 |
| `server.py [--host H] [--port P]` | 启动写入服务 |
| `server.py --selftest` | 界面不变量自检（不开服务） |
| `server.py --audit` | 落盘数据体检（只读、可重跑） |
| `server.py --rebuild-index` | 只重建派生索引 |
| `server.py --repair <pid> [--apply]` | 定点修复可疑字段 |
| `test_mastery.py` | 掌握与冷却状态机验收 |

## 数据布局

```
<用户数据目录>/  数据目录（ADR 0008）：Windows %APPDATA%\ai-note、
                 macOS ~/Library/Application Support/ai-note、其它 ~/.local/share/ai-note
  pages/         整页原图留底（永不裁剪母本，切分错了可以重切）
  assets/        题面裁剪图 + <id>-clean.png（擦除后）+ <id>-cleanmask.png（掩膜可视化）
  problems/      一题一个 JSON —— 「一题一文件」（ADR 0001）
  batches/       打印批次：一叠重做纸的构成快照，页锚点指向它
  inbox/         收件目录：往这里放一个文件就是录入
  index.json     proto 留下的派生索引（新服务不读、不写、不删；索引每次请求现算）
  runs/          每次调用的请求与响应留档（含用量）；图片数据不入档
  vocab/         受控词表种子（错因清单 6 条 + 考点大纲 6 个一级节点）

data/            仓库里这一份是**本机测试语料**（同样的结构），只有 vocab/ 的两个种子进仓库
runs/            旧位置留下的留档；新默认已跟着数据目录走
samples/         真实照片样例（已在 .gitignore 中）
```

数据目录由 `--data` / `AI_NOTE_DATA` 指定，留档目录由 `AI_NOTE_RUNS` 单独覆盖；留档用于回看提示词效果与花销——**图片数据被替换为 `<image omitted>` 占位**。
把仓库里那份语料搬去当生产数据是一次 `cp -r data/. ~/.local/share/ai-note/`（Windows 换成 `%APPDATA%\ai-note`）。
为什么运行时的文件不再放项目根、`data/` 为什么降级为测试语料：见
[ADR 0008](docs/adr/0008-runtime-files-live-in-the-user-data-dir.md) 与 [`data/README.md`](data/README.md)。

## 设计原则

来自十轮实测，几条贯穿始终的原则（ADR 0007「项目宪法」）：

1. **不许静默。** 每个响应都带「我做了什么、我没做什么」的显式字段（警告、被跳过的原因、降级说明）；**每个由人填写的字段都要有一个会喊的检查**。这是实测里唯一反复出现的失败模式——同一类病反复出现在题型枚举、探针判词、界面更新、验收与题卡的漂移上。
2. **低置信度一律落向「看不清」，绝不落向「对」。** 假「对」会让你从此不再看到这道题，假「错」只是浪费你一点时间——两者代价不对称（`CONTEXT.md`「看不清」）。
3. **先入库后审核。** 未审核的题不进重做纸，标准答案不参与自动判定（`CONTEXT.md`「未审核」）。
4. **数据以文件为源。** 文件即数据库，git 充当版本历史与备份。索引是派生物，随时可重建（ADR 0001）。
5. **判定分层。** 选择/填空可机判，解答/证明题只能人工确认。纸上重做一律人工判定（`CONTEXT.md`「自动判定」「人工确认」）。

## 已知限制与债务

**功能缺口**（已在「实现状态」表列出，此处补充细节）：

- **一次只能处理一道题**。一页多题要靠 `--note` 指认，批量切分与红笔筛选未实现。
- **考点大纲是种子数据**（形状 `{"大纲": {<科目>: {<章>: {<节>: [<点>]}}}}`，只为验证受控词表机制，**不是完整大纲**），一门科目、6 个章、每章 2–3 个节。
- **错因清单 6 条**：概念不清、方法不会、计算失误、审题错误、时间不够、抄写错误。
- **判定只看最终答案，不看过程**。
- **活页纸是浏览器打印的 HTML**，不是 PDF 生成；打孔与双面未考虑。
- **页锚点是短码 + 本机网址，不是二维码**；「扫一下打开」未实现（它不承担回写，只是快捷入口）。
- **切分的模型层还没接**（`segmenter` 注入点是空的）：建页与重切都会明说「切分不可用」。
  页**照建**、`blocks` 是 `null`（不是 `[]`），人可以在照片上自己画框——机器切分只是**预设**。
- **复习纸未实现**。

**已知债务**（代码注释或 ADR 里明确记录的）：

- **`ANCHOR_BASE` 在绑 `0.0.0.0` 时会生成手机访问不到的地址**（ADR 0007 决定 5 明确记录为「一处已查到的债务」）。手机访问需要显式可配的「对外地址」，新后端已纳入契约，原型里未修。
- **目录监视在多平台上依赖轮询**（`inotify` 在某些挂载与同步盘上不可靠）。ADR 0007 要求「监视」必须有一个可手动触发的等价入口——原型里未实现监视，也未实现手动入口。
- **无依赖锁定文件**。`requests` / `pillow` / `numpy` 均未固定版本。
- **源码头部没有许可证声明**（根目录有 [`LICENSE`](LICENSE)，MIT）。
- **界面没有鉴权**，也不该暴露到公网（ADR 0003 单用户本机）。
- **验收只实现了探针与等价性判定那一半**；抽取角色的「直接可用率」需要人自己数。
- **擦除是半自动的**：机器给初值，掩膜要人在界面上过一遍（ADR 0005）。全自动需要自训分割模型；本机无 GPU 驱动，且已知的开源方案 [DocUnfold](https://github.com/CXH-Research/DocUnfold) 未发布预训练权重（`docs/acceptance-log.md` 第五轮已核实）。

## 接下来的方向

- **接上切分的模型层**：`segmenter` 注入点还是空的（`server/app.py` 的 `build_server` 默认不注入），
  上岗要过三关：视觉探针、固定照片集上的人工判定、三条对账判据。
- **前端重构**：规格在 [issue #17](https://github.com/Nether-wart/ai-note/issues/17)，标签 `ready-for-agent`
  ——索引以**科目**为根，录入与阅读都在同一个前端里走完（[ADR 0009](docs/adr/0009-runtime-sidebar-not-docs-plugin.md)）。
- **生产前端**：以 Docusaurus 为基、保持可编译为 Tauri（ADR 0006）。数据不进构建产物；写入服务退化为纯 JSON API。
- **后端重写**：契约优先，`proto/server.py` 只作原型的证据（ADR 0007）。实现语言暂时仍是 Python，因为十轮实测的资产（擦除、闸门统计、裁剪体检、逐字打磨过的提示词与阈值）都在 Python 里。

## 文档索引

| 文档 | 内容 |
|---|---|
| [`CONTEXT.md`](CONTEXT.md) | 领域术语表——读代码前先读这个 |
| [`AGENTS.md`](AGENTS.md) | Agent skills 入口（Issue tracker / Triage labels / Domain docs） |
| [`docs/adr/0001`](docs/adr/0001-file-backed-data-with-write-service.md) | 文件为源 + 写入服务 + 派生索引 |
| [`docs/adr/0002`](docs/adr/0002-lightweight-multi-tenant.md) | 轻量多租户（**已作废于 0003**，保留记账） |
| [`docs/adr/0003`](docs/adr/0003-single-user-local-first.md) | 单用户本机 + 模型限境内 |
| [`docs/adr/0004`](docs/adr/0004-redo-sheet-prints-transcript.md) | 重做纸印转录文字（**已作废于 0005**，保留替代方案） |
| [`docs/adr/0005`](docs/adr/0005-redo-sheet-prints-cleaned-photo.md) | 重做纸印擦除手写后的原题照片 |
| [`docs/adr/0006`](docs/adr/0006-docusaurus-base-with-tauri-compatibility.md) | 生产形态：Docusaurus 为基 + Tauri 兼容 |
| [`docs/adr/0007`](docs/adr/0007-redesign-the-backend-contract-first.md) | 后端契约优先重设计 |
| [`docs/adr/0008`](docs/adr/0008-runtime-files-live-in-the-user-data-dir.md) | 运行时的文件放用户数据目录，`data/` 只做测试语料 |
| [`docs/contracts/http-api-v0.md`](docs/contracts/http-api-v0.md) | 界面与后端之间的契约 v0：端点、错误信封、字段形状 |
| [`docs/README.md`](docs/README.md) | 文档索引：这些文档该按什么顺序读 |
| [`docs/walkthrough.md`](docs/walkthrough.md) | 人工验收清单：起服务、点完三个页面、哪些行为算红线 |
| [`docs/acceptance-log.md`](docs/acceptance-log.md) | 模型验收记录（十轮，含方法论与量化读数） |
| [`docs/decision-review.md`](docs/decision-review.md) | 决定复核清单与五个漏洞 |
| [`docs/specs/page-segmentation.md`](docs/specs/page-segmentation.md) | 整页切分规格（正文在 issue #2） |
| [`docs/specs/screen-redo.md`](docs/specs/screen-redo.md) | 屏幕重做规格（正文在 issue #1） |
| [`server/README.md`](server/README.md) | 新后端的跑法、模块角色与「不做」清单 |
| [`site/README.md`](site/README.md) | 界面的跑法、架构约束与界面纪律 |
| [`data/README.md`](data/README.md) | 数据目录每一项是什么、哪些不进仓库 |
| [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md) | Issue tracker 约定（GitHub Issues + `gh` CLI） |
| [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md) | Triage 标签映射 |
| [`docs/agents/domain.md`](docs/agents/domain.md) | 领域文档消费方式 |
| [`proto/README.md`](proto/README.md) | 原型说明与跑法 |

## 隐私与合规

错题照片包含手写笔迹，可能包含姓名、学校；未成年人信息属于敏感个人信息。设计上已收窄：

- **单用户本机**（ADR 0003）：没有账号、邀请、登录、数据隔离。写入服务跑在自己机器上；手机经 Tailscale 或局域网访问（**但见上文 `ANCHOR_BASE` 债务**）。
- **模型调用限境内白名单**（`dashscope`、`deepseek`）：`PROVIDERS` 是硬编码常量，指到白名单外直接拒绝启动。
- **密钥不进仓库**：`.env.local` 与 `~/.env.local` 都认，前者已在 `.gitignore` 中。
- **照片默认留在本机**：`samples/` 已在 `.gitignore` 中；只有调用模型的那些请求会把图片送到境内模型服务。

但**这仍然是「自己的数据自己管」，不要把它暴露到公网**。

## 贡献

Issue 与 spec 住在 GitHub Issues 里，用 `gh` CLI 操作，详见 [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)。

注意：`origin` 走的是 gh-proxy 镜像（`https://v4.gh-proxy.org/https://github.com/nether-wart/ai-note`），`gh` 无法从 remote 推断 host/owner/repo。**所有 `gh` 命令必须显式传 `-R nether-wart/ai-note`。**

Triage 标签用 [`docs/agents/triage-labels.md`](docs/agents/triage-labels.md) 里映射的五档：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。

## 许可证
MIT
