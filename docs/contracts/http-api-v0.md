# HTTP API 契约 v0（只读）

> 状态：**v0，只读**。这份文档是后端与界面之间唯一必须跨得过去的东西
> （[ADR 0007](../adr/0007-redesign-the-backend-contract-first.md) 第 1 条：契约优先）。
> 实现落在 `server/`，界面落在 `site/`。将来换语言重写实现、或把服务塞进 Tauri 进程，
> 换的是实现，不换这份契约。
>
> **基线**：`v0 基线 md5 5cdaf75`（= commit `765cd31`）。此后每次改这份文件，
> 都在 §11 的「变更记录」里记一笔（改了什么、为什么），别让契约悄悄漂。
>
> 覆盖工单：[#3 后端骨架与契约 v0](https://github.com/nether-wart/ai-note/issues/3)（本文件）
> 服务、被服务、被解阻的工单：[#5](https://github.com/nether-wart/ai-note/issues/5)
> [#7](https://github.com/nether-wart/ai-note/issues/7)
> [#9](https://github.com/nether-wart/ai-note/issues/9)
> [#13](https://github.com/nether-wart/ai-note/issues/13)。

## 0. v0 的范围

| | v0 |
|---|---|
| 有 | 三个只读端点：读索引、读一题、读图片。全部 JSON（图片字节是唯一例外，见 §7.1） |
| 有 | `GET /upload`（后端托管的**单文件**手机上传页）、`POST /api/inbox`（往收件目录放文件）、`POST /api/inbox/scan`（目录监视失效时的手动等价入口）——#13，见 §10.3 |
| 有 | `GET /api/brief/<科目>` 与 `POST /api/brief/<科目>`（**本版（前端重构 / #17）新增**）：读该科目最近一份简报、按需生成一份。生成走 `brief` 角色，且必须先过**数字闸门**（每个数字都要能在索引里逐字找回），见 §10.5 |
| 有 | 每个响应带 `warnings[]`，以及「我跳过了什么」的 `skipped[]`（ADR 0007 第 6 条：不许静默） |
| 有 | 屏幕重做的硬闸门读数（有擦除图 **且** 可自动判定）与「另有 N/M 道进不来」的两个显式数字（§6.1） |
| 有 | 「这道题能不能走自动判定」的**唯一一份**实现（三拒绝理由，见 §6）——#5 直接复用，不许另写 |
| 无 | 改已有题卡的写端点只有 `POST /api/attempt/<pid>` 一个入口，现有两种形态：**屏幕重做**（#5）与**定点修正**（#6），形状见 §10.1；纸上重做（`channel:"paper"`）仍未实现。`POST /api/inbox*` 只往收件目录放**新**文件（#13），它不改任何题卡／资产／索引 |
| 无 | **渲染页面**。ADR 0007 第 2 条把两件事分开说：后端**不负责渲染**（不做模板、不管界面状态），但它**托管静态资源**（手机上传页、图片、前端产物）——**托管文件不是渲染**。所以 v0 出 JSON 与静态图片字节，并托管那个单文件的手机上传页（§10.3）：页面里没有任何服务端注入的值，请求一律走同源相对路径 |
| 无 | 索引落盘。v0 每次请求**现算**（索引是派生数据，ADR 0001）；写盘归写端点 |

术语一律照 `CONTEXT.md`（**题卡**、**掌握**、**冷却**、**判定**、**看不清**、**默认打印清单**、
**自动判定**、**未审核**、**原答**、**订正**）。本文件不发明新词。

## 1. 基址、监听与配置

- 默认 `http://127.0.0.1:8765`，**只监听本机**（ADR 0003 单用户本地优先）。
- 数据目录默认是**用户数据目录**（[ADR 0008](../adr/0008-runtime-files-live-in-the-user-data-dir.md)）：
  Windows `%APPDATA%\ai-note`、macOS `~/Library/Application Support/ai-note`、
  其它 `$XDG_DATA_HOME/ai-note`（没设就是 `~/.local/share/ai-note`）。
  用 `--data <dir>` 或环境变量 `AI_NOTE_DATA` 覆盖。仓库里的 `data/` 只是**测试语料**，
  不是默认数据目录（要拿它起服务就显式 `--data data`）。
  测试与验收必须指到临时目录，**绝不指向真实数据目录去写**。
  v0 不写任何文件，所以「指到真实数据目录」也只读。
- 启动：`python3 -m server.app --data <dir> --host 127.0.0.1 --port 8765`。
- **对外地址**（手机要打开的链接、页锚点）必须显式可配，不许从 `--host` 推导
  （ADR 0007 第 5 条记的是一处已查到的债务：绑 `0.0.0.0` 时推导出来的地址手机访问不到）。
  参数是 `--public-base`（或 `AI_NOTE_PUBLIC_BASE`），默认按**绑定之后**的 `host:port` 推导
  （`--port 0` 时端口是系统给的，先算会算错）。
  **它必须有暴露面**，否则界面拼不出手机能打开的链接：读在 `data.server.public_base`（§3）。
  推导出来的地址如果**别的设备打不开**（通配地址／环回，而服务又绑在通配地址上），
  索引里会出现 `public_base_not_reachable` 警告（§8）——这是债务的可见化，不是错误。
- **收件目录**（CONTEXT「收件目录」）：`--inbox`（或 `AI_NOTE_INBOX`），默认是**数据目录**
  下面的 `inbox/`（`--data <dir>` → `<dir>/inbox/`）。它**跟着 `--data` 走**：
  换了数据目录却还往仓库里的 `data/inbox` 写，就是往真数据里写。
  「往这里放一个文件」是录入的唯一入口（ADR 0007 第 4 条）。上传、扫描与上传页见 §10.3。
- **模型角色的配置**：每个角色一对 `<角色>_PROVIDER` / `<角色>_MODEL` 环境变量
  （`.env.local` 可覆盖；读法只有 `server/config.py: load_role_config` 一处）。
  provider 不在白名单里是**坏配置**：服务拒绝启动，不是每个请求里再验一遍。

  | 角色 | 环境变量 | 默认 | 上岗闸门 |
  |---|---|---|---|
  | 抽取 `extract` | `EXTRACT_PROVIDER` / `EXTRACT_MODEL` | `deepseek` / `deepseek-flash` | 先过视觉探针，再看真实照片上的直接可用率 |
  | 判定 `judge` | `JUDGE_PROVIDER` / `JUDGE_MODEL`（另有 `JUDGE_THRESHOLD`） | `deepseek` / `deepseek-flash` | 等价性考卷**全对**（模型自出的与人手写的两份都跑） |
  | 切分 `segmenter`（**本版（前端重构 / #17）新增**） | `SEGMENTER_PROVIDER` / `SEGMENTER_MODEL` | **沿用抽取角色的默认值** | 三条**都**必须过：视觉探针 + 固定照片集上的人工判定 + `server/segmentation.py` 的三条确定性对账判据，见 §12.1 |
  | 简报 `brief`（**本版（前端重构 / #17）新增**） | `BRIEF_PROVIDER` / `BRIEF_MODEL` | **沿用抽取角色的默认值** | 数字闸门（每个数字都要能在索引里逐字找回），见 §10.5 |

  密钥**不在这里**要求：缺密钥是调用那一刻的 502 `model_unavailable`（可以重试），
  不是启动失败。**换模型就要重跑那个角色的验收，不过考不许上岗**（`CONTEXT.md`「验收」；
  验收脚本与跑法见 §12.1）。简报**不复用抽取角色**：复用会让换模型时两个用途互相绑死（#17 §7）。

### 时间与 id

- 所有时刻是 ISO 8601 带偏移的字符串，原样来自题卡（`created_at` 常是 `+08:00`，
  重做时刻是 `+00:00`）。**要比较的时刻一律先归一化到 UTC**——比原始字符串会把
  `06:31Z` 排在 `09:00Z` 后面（`sort_key` 的注释里记着这个真踩过的坑）。
- 题卡 id 形如 `p-20261004-41c86b`，标题、`warnings` 与 URL 里都用它。
  **id 类**路径参数（题卡 id、页 id）只接受 `^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*$`
  （不许出现 `..`），其余一律 400（防路径穿越，见 §7.3）。
  **科目是另一类路径参数**（`/api/brief/<科目>`）：它是中文，先按 URL 编码收下、
  再拿受控词表**逐字**校验，不过那条字符集规则、也不按文件名拼路径（见 §10.5）。
  一条路径规则的**适用面**要写准：规则只管它管的那一类参数，别的类不拿它当例外。

## 2. 响应信封：每个响应都说清「我做了什么／没做什么」

**所有 JSON 响应**（成功与失败）都是同一个信封，只有两个键的取值不同：

成功：

```json
{
  "ok": true,
  "data": { },
  "warnings": [],
  "skipped": []
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "not_auto_judgeable",
    "reason": "solution_type",
    "message": "解答题只能人工确认：过程题在屏幕上敲不出过程",
    "hint": "这道题只能在纸上重做",
    "details": { "id": "p-20261004-ef7c47" }
  },
  "warnings": [],
  "skipped": []
}
```

`error` 的字段（**每一个错误都有前四个里的前两个**）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `code` | str | 信封级类别，取值见 §9 码表 |
| `reason` | str | **机器可读的细因，任何错误都必有**。一个 `code` 没有更细的原因时 `reason == code`。拒绝的三种理由是 `solution_type` / `no_standard_answer` / `unreviewed`，取自 `server/autojudge.py`——**唯一实现**，界面不许自己再判一遍 |
| `message` | str | 给人看的中文原话。拒绝时**就是** `autojudge.REASONS[reason]` 那句，界面照原话显示，不改写 |
| `hint` | str? | 下一步该怎么办 |
| `details` | object | 点名是哪个参数、值是什么、允许什么 |

**硬规则（编排裁决 D1，起因是原型的一个真坑）**：错误一律是 JSON 信封，**绝不允许**
裸 500、空响应体、或 `text/plain` 的异常回溯。原型的 `chat()` 失败路径是
`die()` = `sys.exit(2)`（`proto/slice.py:214`）——**HTTP 处理器里 `sys.exit` 会杀掉请求**，
新服务一处都不许用（`server/http.py` 只有 `ApiError` 与「兜底 500 也走 JSON 信封」两条路）。

| 字段 | 类型 | 含义 |
|---|---|---|
| `ok` | bool | 是这条请求本身成功还是失败。**注意区分**：「索引建成了，但里面有一张脏卡」是 `ok:true` + `warnings` 非空；「索引根本没建成」才是 `ok:false` |
| `data` | object | 成功时的载荷。失败时该键不存在 |
| `error` | object | 失败时的载荷。成功时该键不存在 |
| `warnings` | Warning[] | 「我做成了，但这些事不对劲」——每条都有一个稳定的 `code` 与一句给人看的 `message` |
| `skipped` | Skipped[] | 「这些东西我没做，原因如下」。**非空时必须与 `warnings` 一起显示给用户**；静默地少一张卡是这个项目最怕的一类失败 |

```jsonc
// Warning
{
  "code": "standard_answer_missing",          // 稳定标识，界面按它决定怎么显示，测试按它断言
  "message": "标准答案为空 → 不能走自动判定，只能人工确认",  // 给人看的原话，界面直接显示
  "id": "p-20261004-ef7c47"                   // 属于哪张卡；索引级警告为 null
}

// Skipped
{
  "code": "problem_file_unreadable",
  "message": "data/problems/p-xxx.json 读不了：JSON 解析失败：Expecting value: line 1 column 1",
  "id": "p-xxx"                               // 认得出 id 就填，认不出为 null
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `code` | str | 稳定标识，界面按它决定怎么显示，测试按它断言 |
| `message` | str | **给人看的完整句子**，不是错误码的复述——界面照原话显示，不改写、不吞掉 |
| `id` | str \| null | 属于哪张卡；索引级的为 `null` |
| `level` | `"warning"` \| `"hint"` | 严重度。`"warning"` = 这里有东西不对，要看；`"hint"` = 提示级（例如「旧卡缺页绑定」，旧数据不该因为新结构变成脏数据）。**级别只有服务能定**，界面不许自行降级。这个键**总是显式发出来**（`hint` 级的码见 §8：`page_binding_missing`、`subject_missing`，以及解答题上的 `standard_answer_missing` 与本版按实现订正的那句说明）——有默认值却不出现在响应里，下游只能靠猜 |

**`skipped` 与 `warnings` 的分工（不要混）**：

- `warnings` = **我建成了这条记录，但有些事不对劲**（脏字段、串题、缺图）。
- `skipped` = **我根本没建出这条记录**（文件读不了、不是 JSON 对象、缺必需字段）。
  它比 `warnings` 重：少一张卡不能只靠一句警告带过。两条都有码表（§8）。

`code` 是给机器看的，允许新增，不允许悄悄改语义。

## 3. `GET /api/index` —— 读索引

列出全部题卡，按**默认打印清单**的顺序排好（见 §4）。

**请求**：无查询参数、无 body。

**成功响应**（`data`）：

```json
{
  "built_at": "2026-10-04T09:39:57+00:00",
  "count": 2,
  "problems": [ /* Problem，见 §3.1 */ ],
  "stats": {
    "problems": 2,
    "problems_skipped": 0,
    "in_default_list": 0,
    "cooling": 2,
    "graduated": 0,
    "auto_judge_eligible": 1,
    "auto_judge_ineligible": 1,
    "by_subject": {
      "数学": { "problems": 1, "in_default_list": 0, "cooling": 1,
                "graduated": 0, "auto_judge_eligible": 1, "unreviewed": 1 }
    },
    "unclassified": 1
  },
  "subjects": ["数学"],
  "outline": {
    "数学": { "函数与导数": { "导数与单调性": ["含参单调性"], "极值与最值": [] } }
  },
  "briefs": {
    "数学": { "latest_date": "2026-10-04",
              "generated_at": "2026-10-04T09:39:57+00:00",
              "stale": true, "new_problems": 1 }
  },
  "screen_redo": {
    "default_basis": "in_default_list",
    "bases": {
      "in_default_list": {
        "basis_text": "默认打印清单（未毕业且已脱离冷却）",
        "ready": 0,
        "blocked_total": 0,
        "not_auto_judgeable": { "count": 0, "by_reason": [] },
        "no_clean_image": {
          "count": 0,
          "ids": [],
          "message": "另有 0 道因缺少擦除手写后的题面图不能进屏幕重做"
        }
      },
      "including_cooling": {
        "basis_text": "未毕业（含冷却中，「显示冷却中的题」勾上时）",
        "ready": 0,
        "blocked_total": 0,
        "not_auto_judgeable": { "count": 0, "by_reason": [] },
        "no_clean_image": { "count": 0, "ids": [], "message": "另有 0 道因缺少擦除手写后的题面图不能进屏幕重做" }
      }
    }
  },
  "server": {
    "public_base": "http://127.0.0.1:8765",
    "upload_url": "http://127.0.0.1:8765/upload",
    "reachable_from_other_devices": false,
    "inbox": "/abs/path/data/inbox",
    "read_only": true,
    "read_only_note": "题卡／资产／索引只读；唯一的新写入是 POST /api/inbox 往收件目录放**新**文件（#13），它不改任何已有数据"
  },
  "warnings": []
}
```

- `built_at`：本次现算的时刻（v0 不落盘，所以它同时是「这份数据有多新」）。
- `problems`：**已经排好序**。界面按原样渲染即可，不许再排一次
  （排序规则只有一份实现，见 §4）。
- `stats`：从同一批 `problems` 一趟算出来的计数。**分母是全部题卡**（`problems` 里的每一张），
  与 §6.1 里 `screen_redo` 的分母**不是一回事**（那个是某个候选总体）。两处名字相近，
  口径不同，看数字时先看它属于哪一段。
  它是**便利读数，不是第二个真源**：有任何不一致，以 `problems` 为准（测试断言两者一致）。
  逐科目那一层（**本版（前端重构 / #17）新增**）：

  | 键 | 类型 | 含义 |
  |---|---|---|
  | `by_subject` | object | 键是科目名，值是 `{problems, in_default_list, cooling, graduated, auto_judge_eligible, unreviewed}`——六个读数与整表**同名同口径**，只是分母收到一个科目 |
  | `unclassified` | int | **未归类**的**道数**：`subject` 是 `null`／缺字段／空串／纯空白那些卡（那是一等状态，不是缺字段） |

  **不变式（它就是「没有一张卡悄悄没有科目」那条检查）**：
  `sum(stats.by_subject[*].problems) + stats.unclassified == stats.problems`。
  `by_subject` 的键是**词表里的科目 ∪ 卡上出现过的科目**：词表里的科目哪怕 0 道也要有键
  （侧栏要能画出空科目，不然人以为它丢了）；卡上那个不在词表里的科目**也要有键**——
  它已经带着 `subject_unknown` 喊过一声，再从汇总里把它丢掉就是第二次静默，不变式当场就断。
- `subjects`：字符串数组，受控词表的科目清单（`<数据目录>/vocab/subjects.json` 的取值，见 §10.4）。
  **这是「有哪些科目」的唯一来源**（不从文件目录推、不从考点派生，见 §3.1 的 `subject` 行）；
  词表读不出来时给 `[]` 并报词表级警告（§8），**不是**一个安静的空侧栏。
- `outline`：考点大纲的树，形状 `{<科目>: {<章>: {<节>: [<点>, …]}}}`——**点 = 叶子**，
  是字符串数组，**可以是空数组**（空数组 = 这一节还没有点，与「这一节不在大纲里」不是一回事）。
  **只有出现在 `subjects` 里的科目才允许出现在 `outline` 里**，否则报一条 `warning`
  （`outline_subject_unknown`，§8）：两份词表打架要喊出来，不许悄悄以一边为准。
- `briefs`：`{<科目>: {"latest_date": "YYYY-MM-DD"|null, "generated_at": ISO|null, "stale": bool, "new_problems": int}}`。

  | 字段 | 含义 |
  |---|---|
  | `latest_date` / `generated_at` | 最近那一份简报的日期与生成时刻。**这个科目一份简报都没有时两者都是 `null`**——「有没有」由它回答 |
  | `stale` | **这份简报生成之后又有题录进来了**。判据是硬的：该科目里 `created_at` 晚于那份简报的 `covers_until`（它读的那份索引快照，§10.5）的道数 > 0 |
  | `new_problems` | 就是那几道的**道数** |

  侧栏要能显示「简报已过期：有 N 道新题没进去」——**这一句话不许靠界面自己算**，
  所以 `stale` 与 `new_problems` 都由服务给（与 §6.1 第 5 条「那句话由服务给」同一条规矩）。
  没有简报的科目 `stale` 为 `false`、`new_problems` 为 `0`：「还没有简报」与「简报过期了」
  是两件事，混起来侧栏就会对一份不存在的简报喊过期。
- **为什么词表挂在索引里（本版（前端重构 / #17））**：侧栏要一次请求就画出整棵树
  （科目 → 〔简报｜细则｜考点大纲｜今日重做〕），而 `/api/index` 已经是那个「一次拿全」的读端点。
  分成两次请求会露出一个中间态——**有科目但没有大纲**（或反过来），界面那一刻画出来的是半棵树。
  所以不新开端点：受控词表与大纲随索引一起来（形状与来源见 §10.4）。
- `screen_redo`：屏幕重做的两个显式数字（§6.1）。**两套候选总体都算好**摆在 `bases` 里，
  界面按「显示冷却中的题」开关**取**，不许自己重算。
- `server`：服务的自述（ADR 0007 第 6 条要的就是「我做了什么、我没做什么」）。

  | 字段 | 含义 |
  |---|---|
  | `public_base` | **手机真能访问到的地址**（ADR 0007 第 5 条）。页锚点与上传页链接用它拼；它必须**显式可配**（`--public-base` / `AI_NOTE_PUBLIC_BASE`），默认才是按绑定之后的 `host:port` 推导 |
  | `upload_url` | 手机要打开的上传页绝对地址 = `public_base` + `/upload`。**拼法只有一份实现**（`Catalog.public_url`），将来的页锚点走同一个地方，免得有一处忘了用对外地址 |
  | `reachable_from_other_devices` | 这个 `public_base` 是不是「别的设备也能打开」的（通配／环回地址都不是）。这是个**便宜的检查**，不是一次真实探测；它为 `false` 而服务又绑在通配地址上时，`warnings` 里会出现 `public_base_not_reachable` |
  | `inbox` | 收件目录的绝对路径（录入的唯一入口，§10.3） |
  | `read_only` | **已有数据**只读：题卡／资产／索引既不写也不删。#13 之后唯一的新写入是往收件目录放**新**文件 |
  | `read_only_note` | 上面那句话的原话。`read_only: true` 与「有一个写端点」并存，不解释清楚就是静默 |
- `data.warnings` 与信封的 `warnings` 是**同一份列表**（同一个数组内容）。
  两个位置都有，是为了让「按端点取警告」和「按信封统一取警告」两种写法都对。
  索引级警告与逐卡警告都在这一个平铺列表里，逐卡警告同时**也**出现在那张卡的
  `warnings` 里（同一份内容，两处放：列表页按卡渲染不必再筛，审计按列表取不必再翻卡）。

### 3.1 Problem 记录（列表条目 = 详情的基础）

字段取自题卡文件，形状与 `proto/server.py` 的 `build_index()` 一脉相承（口径继承，
代码不继承：`proto/` 是冻结的只读证据，不许 import）。

| 字段 | 类型 | 来源 | 备注 |
|---|---|---|---|
| `id` | str | `id` | |
| `created_at` | str | `created_at` | 录入时刻 |
| `subject` （**本版（前端重构 / #17）新增**） | str \| null | `subject`（**题卡顶层字段**） | 这道题的**科目**，取自受控词表 `<数据目录>/vocab/subjects.json`（§10.4）；`null` 表示**未归类**——那是一等状态，不是缺字段。缺字段／空串／纯空白一律算未归类。值是记录的原值：不在词表里也**不许静默改写成 `null`**，只报 `subject_unknown`（§8） |
| `type` | `"choice"` \| `"fillin"` \| `"solution"` | `problem.type` | **题型枚举就是这三个英文词**。写中文键会静默退回默认值——这个坑真踩过 |
| `type_cn` | str | 派生 | 选择 / 填空 / 解答 |
| `transcript` | str | `problem.transcript` | 题面转录 |
| `options` | `{label,text}[]` | `problem.options` | 非选择题为 `[]` |
| `original_answer` | str \| null | `original_solution.original_answer` | **原答**。看不出来时是 `null`，绝不猜 |
| `correction` | str \| null | `original_solution.correction_transcript` | 订正 |
| `original_transcript` | str \| null | `original_solution.transcript` | 原解 |
| `present` | bool \| null | `original_solution.present` | 有没有手写原解 |
| `standard_answer` | str \| null | `standard_answer.value` | **标准答案**，自动判定的唯一基准 |
| `correct_solution` | str \| null | `correct_solution.text` | **正解** |
| `topics` | str[] | `topics` | 考点（大纲叶子路径） |
| `error_causes` | str[] | `error_causes` | 错因 |
| `new_tag_proposals` | str[] | `new_tag_proposals` | 待批的新标签提案 |
| `review` | `"reviewed"` \| `"unreviewed"` | `review.status` | |
| `reviewed_at` | str \| null | `review.reviewed_at` | |
| `review_reopened_because` | str \| null | `review.reopened_because` | 被自动修复打回重新确认的原因。不许静默：**打回过就必须看得见** |
| `mastery` | `{state,streak,last_attempt_at}` | `mastery` | 状态机原始读数 |
| `mastery_cn` | str | 派生 | 在池 / 毕业 |
| `streak` | int | 派生 | 连续正确次数 |
| `graduated` | bool | 派生 | `mastery.state == "graduated"`（**毕业**） |
| `cooling` | bool | 派生 | 距上次重做不满 7 天。**从未重做过的以录入时间起算** |
| `cooldown_days` | int | 派生 | 还要几天脱离冷却；不在冷却为 `0` |
| `in_default_list` | bool | 派生 | `not graduated and not cooling`——**默认打印清单**的成员资格 |
| `excluded_from_default_because` | `"graduated"` \| `"cooling"` \| null | 派生 | 被排除的**原因**。在清单里时为 `null`。两个原因同时成立时取 `"graduated"` |
| `last_verdict` | `"correct"` \| `"wrong"` \| `"unreadable"` \| null | `attempts[-1].verdict` | |
| `last_verdict_cn` | str \| null | 派生 | 对 / 错 / 看不清 |
| `attempts` | int | `len(attempts)` | 重做次数 |
| `attempt_log` | Attempt[] | `attempts[-6:]` | 最近 6 次（见 §3.2） |
| `sort_key` | str | 派生 | 归一化到 UTC 的排序键（§4） |
| `cells` | int | `print.cells` 或题型默认 | 版面格 |
| `cells_source` | `"manual"` \| `"default"` | `print.cells_source` | 格子数是谁定的 |
| `images` | `{original,clean,mask}` | 派生 | 三个 URL 或 `null`（§5） |
| `has_clean` | bool | 派生 | 有没有**擦除手写后的题面图**。没有的题不进屏幕重做、也不进重做纸 |
| `auto_judge` | `{eligible,reason,reason_text}` | 派生 | 能不能走自动判定（§6） |
| `screen_redo` | `{ready,blockers,blocker_text}` | 派生 | 能不能进**屏幕重做**（§6.1）。两条硬闸门：有擦除图 **且** 通过自动判定。`blockers` 是**全部**原因，不是第一个 |
| `warnings` | Warning[] | 派生 | 这张卡自己的自检结果（码表见 §8，形状见 §2） |

### 3.2 Attempt（重做记录，摘要形态）

```json
{
  "at": "2026-10-04",
  "channel": "screen",
  "channel_cn": "屏幕重做",
  "verdict": "correct",
  "verdict_cn": "对",
  "source": "auto",
  "source_cn": "自动判定",
  "confidence": 0.93,
  "provider": "deepseek",
  "model": "deepseek-flash",
  "error_causes": ["计算失误"],
  "note": "判对且脱离冷却：连续正确 1/2"
}
```

`at` 在摘要里截到日期（`YYYY-MM-DD`）；完整时刻见 §5 的 `attempts_detail`
——**它就是卡里 `attempts` 的原样、不裁剪**，所以 `provider` / `model` / `overrode`
这些字段也在里面（裁决 D2、#5 验收第 2 条）。

- `source` 的取值**只有两个机器可读的值**：`"auto"`（自动判定）与 `"human"`（人工确认），
  中文渲染另走 `source_cn`（编排裁决 D2，沿用原型的取值）。改判（#6）后 `source` 变成
  `"human"`，原来那条判定留在 `overrode` 里——**「这条判定原本是谁给的」永远可查**
  （`overrode` = `{verdict, source, confidence, provider, model}`，见 §10.1）。
- `provider` 与 `model` 是**两个**字段（`"deepseek"` / `"deepseek-flash"`），
  不许拼成一个 `"provider/model"`（编排裁决 D2）。理由：「换判定模型必须重跑考卷」
  这句话要求模型身份可查、可比。

## 4. 排序与默认打印清单：只有一份实现

`/api/index` 的 `problems` 顺序 = **默认打印清单的顺序**（`CONTEXT.md`「默认打印清单」）：

1. **排序键**：上次重做时刻；**从未重做过的以录入时间起算**；统统归一化到 UTC 后
   按从早到晚升序（最早的最该做，排最前）。
2. **成员资格**：`in_default_list == true`，即未毕业且已脱离冷却。
3. 「显示冷却中的题」开关 = 界面不过滤 `cooling`；它**不改变顺序**，
   这类重做照常记录但不推进掌握——那是状态机的事（#5），不是排序的事。
   这个开关连带改变 §6.1 里 N/M 的候选总体，所以服务把两套数字都算好（`bases`）。

界面**不重新实现**这套规则：它拿 `in_default_list` 与 `sort_key` 当**读数**用。

**队列是快照，不是实时视图（#7 必须照这条做）。** 重做页把这一批题放在 URL 里
原样传递（`/redo?queue=<题目列表>&i=<第几题>`），服务端**不参与**队列的维护，也不
「现算第 i+1 题」。理由是硬的：**判完一道题的瞬间它进了冷却、从默认打印清单里消失了**，
现算出来的第 i+1 题会漂到别处去。第九轮那条教训在这里同样适用——越界必须明确失败，
绝不悄悄落到别的题上。
规则的中文口径与边界（判错立刻回池、冷却只挡「计入正确」）在 `docs/adr/0001`…
之外的 `CONTEXT.md` 词条里，参考实现见 `proto/slice.py` 的 `cooldown_until`
与 `proto/test_mastery.py`（假时钟、12 条断言、不联网）。

## 5. `GET /api/problem/<pid>` —— 读一题

**成功响应**（`data`）= §3.1 的 Problem 记录**超集** + 四个详情专属字段：

```json
{
  "id": "p-20261004-41c86b",
  "type": "choice",
  "images": {
    "original": "/api/problem/p-20261004-41c86b/image/original",
    "clean": "/api/problem/p-20261004-41c86b/image/clean",
    "mask": "/api/problem/p-20261004-41c86b/image/mask"
  },
  "attempts_detail": [],
  "source": {
    "page_image": "data/pages/41c86bcfc007.png",
    "bbox_norm": [0.02, 0.12, 0.76, 0.28],
    "bbox_px": [3, 20, 541, 79],
    "original_file": "2.png"
  },
  "clean": {
    "method": "erase_ink",
    "boxes_norm": [[0.58, 0.05, 0.08, 0.22]],
    "manual": { "add": [], "drop": [] },
    "mask_px": 3442,
    "colored_px": 211,
    "residual_px": 0,
    "dropped_px": 1398,
    "health": { "residual_color_px": 0, "print_holes_px": 0 }
  },
  "provenance": {
    "role": "extract",
    "provider": "deepseek",
    "model": "deepseek-flash",
    "extract_warnings": ["照片上有订正/批注，但没有可信的原答 → 原答留 null，需人工确认"],
    "confidence": { "standard_answer": 0.9, "original_answer": 0.2 }
  }
}
```

| 详情专属字段 | 含义 |
|---|---|
| `attempts_detail` | **全部**重做记录（不是最近 6 条）。**就是卡里 `attempts` 的原样，不裁剪**：`{at,channel,verdict,source,confidence,provider,model,error_causes,note,overrode?}` |
| `source` | 这张卡从哪来：整页照片、归一化与像素边界、原始文件名。**没有就是 `null`**（存量或手工录入的卡）。⚠ 这里的 `bbox_norm` / `bbox_px` 是**整页**坐标 |
| `clean` | 擦除手写这一趟的产物读数。没有就是 `null`。字段见下 |
| `provenance` | 是哪次模型调用造出这张卡的（角色／模型／当时的警告／逐字段置信度）。没有就是 `null` |

`clean` 里的字段：

| 字段 | 含义 |
|---|---|
| `method` | 擦除是怎么做的（`"erase_ink"`）。换方法就换这个值，别悄悄改行为 |
| `boxes_norm` | **这一趟认定的手写掩膜框**（`[x, y, w, h]` 归一化）。⚠ **裁剪图坐标**，不是整页坐标 |
| `manual` | 你在审核时手工增删的框：`{add: [...], drop: [...]}`，同一套**裁剪图坐标**。空对象也要给（`{"add": [], "drop": []}`），别用 `null` 表示「没有」——那与「没跑过擦除」混在一起了 |
| `mask_px` / `colored_px` / `residual_px` / `dropped_px` | 掩膜、彩笔、残留、被丢掉的像素数。框的**个数**不单列：`len(boxes_norm)` |
| `health` | 擦除体检读数（残留彩笔、印刷体被伤到的孔洞等） |

> ⚠ **两套坐标基准，不许混用**：`source.bbox_*` 是**整页**坐标，
> `clean.boxes_norm` / `clean.manual` 是**裁剪图**坐标。任何「把块画到原图上」或
> 「把掩膜框画到页上」的地方（尤其 #14）**必须显式换算**。

**「超集」是一条硬承诺**：列表页与详情页对同一道题说不同的话，是这个项目已经出过的
那类事故（界面把第一题的文字写进了第二题）。所以两者由**同一个**记录构造函数产出，
测试逐字段断言 `index.problems[i] ⊂ problem(j)`。

### 5.1 失败形状

| 情况 | HTTP | `error` |
|---|---|---|
| 没有这张卡 | 404 | `{code:"not_found", message:"没有这道题：p-xxx", hint:"GET /api/index 看有哪些", details:{what:"problem", id:"p-xxx"}}` |
| id 非法（含 `/`、`..`、**空**） | 400 | `{code:"bad_request", message:"题卡 id 非法：'../../etc/passwd'", hint:"id 只允许字母、数字、点、下划线与连字符", details:{param:"pid", value:"../../etc/passwd"}}` |
| id 是空的（`GET /api/problem/`） | 400 | 同上，`message` 是 `题卡 id 非法：''`。**不是 404**：客户端少给一段路径是客户端 bug，404（「没这条路由」）会让人去翻路由表 |
| 用错方法（POST 到这个只读端点） | 405 | `{code:"method_not_allowed", message:"...", details:{allowed:["GET","OPTIONS"]}}` |

## 6. 能不能走自动判定：一处实现，三个拒绝理由

`auto_judge` 是请求一个**服务端读数**，不是客户端自己算：

```jsonc
{ "eligible": true,  "reason": null, "reason_text": null }
{ "eligible": false, "reason": "solution_type",
  "reason_text": "解答题只能人工确认：过程题在屏幕上敲不出过程" }
```

三个理由，**按这个优先级依次判**（同时成立时取前面的那个，保证同一张卡永远给同一个**判定**）：

| 优先级 | `reason` | 触发 | `reason_text`（界面照原话显示） |
|---|---|---|---|
| 1 | `solution_type` | `type == "solution"` | 解答题只能人工确认：过程题在屏幕上敲不出过程 |
| 2 | `no_standard_answer` | 标准答案为空 | 这道题没有标准答案 → 没有判定的基准，只能人工确认 |
| 3 | `unreviewed` | `review == "unreviewed"` | 这道题还没审核 → 标准答案还不可信，不参与自动判定 |

三者都成立时给 `solution_type`。**这套判断只有这一份实现**（`server/autojudge.py`）：
界面照 `reason_text` 原话显示，#5 的写端点拿同一个函数决定收不收这次作答。

> 为什么单列出来讲：`proto/slice.py` 里已停用的命令行判定里已经有前两条，
> 如果新端点另写一遍，两处迟早各判各的——而「题型枚举静默退回默认值」正是这个
> 项目反复被咬的那类失败。

### 6.1 屏幕重做的硬闸门：缺擦除图不得进队列，也不得退化成原图

「能自动判定」**不等于**「能进屏幕重做」。进屏幕重做必须同时满足：

1. **有擦除手写后的题面图**（`data/assets/<pid>-clean.png` 这一族）。
2. **通过 §6 的自动判定**（已审核 + 有标准答案 + 非解答题）。

两条都是 `and`。原型在 `proto/server.py:235` 写的是 `has_clean or review == "reviewed"`
（**是 `or`**），于是「已审核但没有擦除图」的卡会过关、渲染成 `<img src="None">`。
编排裁决 D4 把这条堵死：

- **缺擦除图不许回退到原图**——原图印着订正，把答案摆在做题的人面前，重做就没有意义了。
  所以契约里没有任何「clean 取不到就给 original」这样的兜底，`images.clean` 取不到时是 `null`。
- **也不许静默丢掉**。缺擦除图的道数由索引级汇总**显式**报出来（见下），与「不能自动判定」
  的道数**并列**显示。

单张卡的读数（`Problem.screen_redo`）：

```jsonc
{ "ready": true,  "blockers": [], "blocker_text": [] }
{ "ready": false, "blockers": ["solution_type", "no_clean_image"],
  "blocker_text": ["解答题只能人工确认：过程题在屏幕上敲不出过程",
                   "缺少擦除手写后的题面图 → 不能进屏幕重做（也不许退回原图：原图印着订正）"] }
```

`blockers` 是**全部**阻塞原因，不是第一个——一张题可以同时踩两个坑，界面要能一次说清。
`blocker_text` 与它一一对应，界面照原话显示。

「擦除手写后的题面图」是哪一张：`data/assets/<pid>-clean.png` 这一族
（手写掩膜是 `<pid>-cleanmask.png`）。**没有第二张可以替代**——见下。

索引级汇总（`data.screen_redo`，编排裁决 D3/D4）。**两套候选总体都算好**：

```jsonc
{
  "default_basis": "in_default_list",     // 开关没勾时用这一套
  "bases": {
    "in_default_list": {                  // 默认打印清单：未毕业且已脱离冷却
      "basis_text": "默认打印清单（未毕业且已脱离冷却）",
      "ready": 1,                         // 这个总体里真正能进屏幕重做的
      "blocked_total": 3,                 // 至少有一个 blocker 的卡数（去重）
      "not_auto_judgeable": {             // 「另有 N 道不能自动判定」的那个 N
        "count": 2,
        "by_reason": [
          { "reason": "solution_type", "reason_text": "解答题只能人工确认：…",
            "count": 1, "ids": ["p-…"] },
          { "reason": "unreviewed", "reason_text": "这道题还没审核 → …",
            "count": 1, "ids": ["p-…"] }
        ]
      },
      "no_clean_image": {                 // 「另有 M 道缺擦除图」的那个 M
        "count": 2, "ids": ["p-…", "p-…"],
        "message": "另有 2 道因缺少擦除手写后的题面图不能进屏幕重做"
      }
    },
    "including_cooling": { /* 同形状。总体 = 未毕业（含冷却中） */ }
  }
}
```

四条口径，写死在契约里免得 #7 与界面各算各的：

1. **候选总体由开关决定，服务把两套都算好**：不勾「显示冷却中的题」用
   `in_default_list`（未毕业且已脱离冷却），勾上用它旁边那套 `including_cooling`
   （未毕业、含冷却中）。界面按开关**取**数字，**不许重算**——一处实现，一个真源。
   两套里都排除毕业的卡：这个开关管的是冷却，不是毕业。
2. **N 的口径**：这个总体里因为**不能自动判定**（§6 的三个理由）而进不了队列的卡数。
   一张冷却中的解答题在不勾时根本不在总体里——它不是「因为不能判定才没进」，
   把它算进 N，页头那句话就在撒谎。
3. **N 与 M 可以重叠**：一张既无擦除图、又是解答题的卡同时出现在两个列表里，
   因为两句话都真的成立。`blocked_total` 是**去重**的卡数，所以允许 `N + M > blocked_total`。
4. **真源是每张卡的 `screen_redo.blockers`**；`by_reason` 与 M 只是从同一批记录一趟算出来的
   便利读数。有任何一个数字对不上，以 `problems` 为准。
5. **M 的那句话由服务给**（`no_clean_image.message`），界面**照原话显示，不再自己拼一遍**
   ——拼了就会出现两句措辞几乎一样的话（里程碑二第一次跑真实渲染时就撞上了这个重复）。
   N 没有现成句子，因为它的构成（三个理由的分布）只有界面知道该怎么排版：
   界面拼 N 时**必须逐个列出理由码与道数**（裁决 D3），不许只说「另有 N 道」。

## 7. `GET /api/problem/<pid>/image/<kind>` —— 读图片

`kind` ∈ `original`（原题裁剪图）｜`clean`（**擦除手写后的题面图**）｜`mask`（手写掩膜）。

三张图各自对应磁盘上的哪个文件，由题卡里的字段决定（`Problem.images` 已经把 URL 给全）：

| `kind` | 来源 |
|---|---|
| `original` | `problem.image`（如 `data/assets/<pid>-problem.png`） |
| `clean` | `problem.clean_image`（如 `data/assets/<pid>-clean.png`） |
| `mask` | **约定文件名** `<pid>-cleanmask.png`（题卡里没有这个字段）。只有卡里有擦除图时才存在 |

**成功**：直接是图片字节。

```
HTTP/1.1 200 OK
Content-Type: image/png
Content-Length: 51234
Cache-Control: no-store
X-Ai-Note-Image-Kind: clean
```

| 响应头 | 值 | 为什么 |
|---|---|---|
| `Content-Type` | 按后缀：`.png`→`image/png`，`.jpg/.jpeg`→`image/jpeg`，`.webp`→`image/webp` | 只有这四种后缀会被服务，其余 404 |
| `Cache-Control` | `no-store` | 数据是私人的、会随重做与审核变化；缓存一份擦除前的题面图会泄露答案 |
| `X-Ai-Note-Image-Kind` | `kind` | 回显请求，方便在浏览器的网络面板里认清这是哪一张 |

`<img src>` 直接用；两个 `kind` 的 URL 在 Problem 记录的 `images` 里给全，界面不许自己拼路径。

### 7.1 「全部 JSON」的**唯一**例外，以及它怎么记账

工单 #3 写的是「三个只读端点……全部 JSON」。图片字节不可能是 JSON——这是**唯一**的例外，
已在 issue #3 上单独评论记下（硬规则：契约偏离必须留痕）。依据是两份 ADR 的合并结论：
ADR 0006「服务只出 **JSON 与静态图片**」，ADR 0007 第 2 条把「托管静态资源（图片）」与
「渲染页面」明确分开。

所以「不许静默」在这条端点上的落法是：

- **成功**：字节 + 上面三个头。图片本身没有「我跳过了什么」可言，
  而关于这张图的警告（缺擦除图、卡里记了但文件不在）**全部**长在
  `Problem.warnings` 上，界面在读 `/api/index` 或 `/api/problem/<pid>` 时就拿到了。
- **失败**：**仍然是 JSON 信封**（§5.1 / §7.2）。一个 404 的 `<img>` 不能是空白——
  它得说清「哪个 kind 没有、有哪几个」。

### 7.2 失败形状

| 情况 | HTTP | `error` |
|---|---|---|
| `kind` 不在枚举里（含**空**：`…/image/`） | 400 | `{code:"bad_request", message:"图片类型非法：'thumb'", details:{param:"kind", value:"thumb", allowed:["original","clean","mask"]}}`。空的时候 `message` 是 `图片类型非法：''`——错要指向 `kind`，不能指向 `pid`（不然人会去查本来是对的题卡 id） |
| 卡里没记这张图／文件不存在 | 404 | `{code:"not_found", message:"这张卡没有 clean 图（擦除手写后的题面图）", details:{what:"image", id:"p-xxx", kind:"clean", available_kinds:["original"]}}` |
| 有这张图，但题卡里没记 URL | 404 | 同上，`message` 补一句「卡里没记 clean_image」 |
| id 非法 | 400 | 同 §5.1 |

`available_kinds` 是**实际能服务的**那几个（URL 记了 **且** 文件真的在），
不是题卡里声明的那几个。声明了但文件不在的情况，在 §7.3 会响。

### 7.3 路径安全

`pid` 只接受 `^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*$`，`kind` 只接受白名单枚举；
解析后的绝对路径必须落在数据目录的 `assets/` 之内，否则 400。
这条不是形式主义：服务将来要经 Tailscale 暴露给手机（#13）。

## 8. 警告码表（v0）

`code` 稳定；`message` 是给人看的原话，允许打磨；`level` 见 §2。
**每一条码都对应一个会喊的检查**（ADR 0007 第 6 条：每个由人填写的字段都要有一个会喊的检查）。

逐卡（出现在 `Problem.warnings` 里。**每个警告对象都显式带 `level` 这个键**；级别只有服务能定：
真矛盾是 `"warning"`，预期状态／「我这一条没查全」是 `"hint"`——下表里 `hint` 级的码逐条标出，
其余为 `"warning"`）：

| `code` | 触发 | `message` |
|---|---|---|
| `standard_answer_missing` （`problem.type == "solution"` 时 `level: "hint"`，其余 `"warning"`——本版按实现改文档，见 #17 §3.9） | 标准答案为空 | 标准答案为空 → 不能走自动判定，只能人工确认 |
| `standard_answer_not_choice_letter` | 选择题的标准答案不是单个选项字母 | 选择题的标准答案不是选项字母：'…' |
| `standard_answer_choice_letter_on_non_choice` | 非选择题的标准答案是单个字母 | 题型是解答，标准答案却只有一个字母 'A' ——像是把选择题的答案填到这道题上了 |
| `original_answer_choice_letter_on_non_choice` | 非选择题的原答是单个字母 | 题型是解答，原答却是一个选项字母 'A' |
| `original_answer_equals_standard_answer` | 原答与标准答案相同 | 原答与标准答案相同（都是 'A'）→ 这是错题本，录进来的是做错的题；两者相同通常意味着订正被当成了原答 |
| `topics_empty` | 考点为空 | 考点为空 → 考点是检索入口 |
| `reviewed_but_incomplete` | 已审核但标准答案或考点为空 | 已标为已审核，但标准答案或考点是空的 |
| `problem_transcript_missing` （`level: "warning"`，**B7 新增**） | 题面还没转录（`problem.transcript` 空） | 题面还没有转录（`problem.transcript` 空）→ 先收录在清单里，审核时填；没有题面就不进重做纸。**#15 的入库建的是骨架卡**（块上只有边界与题号），所以「缺 `problem.transcript`」**不再让索引跳过这张卡**（`skipped` 只剩「连 `problem` 对象都没有」那一档）——落盘了却在清单里看不见就是静默丢题 |
| `no_clean_image` | 没有擦除手写后的题面图 | 没有擦除手写后的题面图 → 不进屏幕重做，也不进重做纸 |
| `clean_image_file_missing` | 卡里记了 `clean_image`，文件不在 | 卡里记了擦除图 …，但文件不在 → 屏幕重做会拿到一个 404 |
| `original_image_file_missing` | 卡里记了题面图，文件不在 | 同上 |
| `page_binding_missing` （`level: "hint"`） | 卡没有页文件绑定（`source.page_image` 缺，或按 §10.2 推出来的页文件不在） | **落地于 #9**（B1）。#9 验收第 2 条：旧卡缺页绑定要报**提示**而不是错误——旧数据不该因为新结构变成脏数据。回填（`python3 -m server.backfill --apply`）之后这条消失 |
| `page_binding_lost` （`level: "warning"`，**B1 新增**） | 页文件**在**，却读不了／不是 JSON 对象，或里面没有任何块绑定这张卡 | 页实体已经在场却对不上账，这是矛盾不是旧数据，所以要**警告**。与上一条同一个判据（`server/pages.py: page_binding`），只有级别不同——**两种缺绑定不许混成一个级别**（#9 验收 2、#15 在它上面扩展） |
| `subject_missing` （`level: "hint"`，**本版（前端重构 / #17）新增**） | 卡上没有科目（**未归类**）：缺 `subject` 字段／`null`／空串／纯空白 | 这张卡还没有科目（未归类）→ 它在侧栏的「未归类」下，不在任何科目的简报里。与 `page_binding_missing` **同级**：录入中途没有科目是**预期状态**，报成 `warning` 会把它淹在噪声里。但它必须在界面上有一栏兜底、把**道数**写出来，**不许把它藏起来**——藏起来就是静默丢题 |
| `subject_unknown` （`level: "warning"`，**本版（前端重构 / #17）新增**） | `subject` 的值不在受控词表里（§10.4） | 科目 '…' 不在科目词表里 → **不许静默把它改写成 `null`**：读数是记录的原值，谁改的谁喊。**只在词表真的读出来时才报**——词表本身读不出来是下面那组词表级警告的事；那时对每张卡喊这一条会把一条真问题淹在一千条噪声里。**这个名字与 §9 里那条 400 同名**，因为**是同一个事实**（这个值不在受控词表里）：那边拒绝一次**输入**（`POST /api/page` 的 `subject`、`POST /api/brief/<科目>` 的科目），这边自检一份**已有数据**。名字一样、码表分开，两处各指认对方（R2/R9：同一个事实不许两个码） |

索引级警告（出现在 `data.warnings` 与信封 `warnings` 里；级别逐条标出，没标的为 `"warning"`）：

| `code` | 触发 | 为什么值得单独响 |
|---|---|---|
| `duplicate_transcript` | 两张卡的题干**逐字相同** | `CONTEXT.md`「串题」：两道不同的题不可能有同一段题干，这条**没有例外**。第八轮那次污染就是这么被抓出来的 |
| `problem_id_mismatch` | 文件名与卡内 `id` 不一致 | 改名事故或复制粘贴事故 |
| `public_base_not_reachable` | 服务绑在通配地址（说明想让别的设备连），而 `data.server.public_base` 是通配／环回地址 | ADR 0007 第 5 条那处**已查明的债**的可见化：绑 `0.0.0.0` 时印 `http://0.0.0.0:8765`，手机打不开。默认只监听本机（`127.0.0.1` + 环回地址）**不**报这条——那是设计如此，报它会训练人忽略警告 |

**受控词表与大纲（本版（前端重构 / #17）新增）**——出现在 `data.warnings` 与信封 `warnings` 里
（索引级，`id` 为 `null`）。词表读不出来**不是 500**，也不许装作「词表就是空的」：
给的是一份空词表 + 这一组里的一条警告——一声不响地给空科目表会让整个侧栏空掉，
而那正是这个项目最怕的静默。

| `code` | `level` | 触发 | 为什么值得单独响 |
|---|---|---|---|
| `subjects_vocab_unreadable` | `warning` | `<数据目录>/vocab/subjects.json` 在却读不了（JSON 解析失败等），`message` 带异常原话 | 侧栏第一级整个空掉；「词表坏了」与「还没建词表」必须分得开 |
| `subjects_vocab_missing` | `warning` | 没有这个文件（形状 `{"科目": ["数学", …]}`，与 `error-causes.json` 同形） | 没有第一级就没有侧栏；`message` 里给出形状，好照它建 |
| `subjects_vocab_empty` | `warning` | 文件在、形状对，但一个科目都没有 | 同上，而这一档更容易被当成「本来就是这样」 |
| `outline_vocab_unreadable` | `warning` | `<数据目录>/vocab/topic-outline.seed.json` 在却读不了 | 考点大纲那一层整个空着（考点本身仍可打在卡上） |
| `outline_vocab_missing` | `hint` | 没有这个文件 | 「还没建大纲」是预期状态，但**要说出来**，不许给一棵安静的树 |
| `outline_subject_unknown` | `warning` | 大纲里的科目不在 `subjects` 里 | 两份词表打架：那个科目下的章／节／点谁都看不到（它不在侧栏的科目下）。以哪边为准是人的决定，服务只负责喊 |

词表级的毛病**一条都不重复到每张卡上**：词表没读出来的时候，逐卡的 `subject_unknown` 一条都不发
（`server/subjects.py: load` 的 `loaded` 就是这道闸）；按 mtime 记住上一次的结果，文件一动就重读
——既不为一千张卡读两千次盘，也不会悄悄一直用旧词表。

收件目录（#13；出现在 §10.3 那两个端点的 `warnings` 里）：

| `code` | `level` | 触发 | `message` |
|---|---|---|---|
| `segmentation_not_implemented` | `warning` | **有页要处理**，但切分（#10）还没接上 | 切分尚未实现（#10）：照片已经收进收件目录，但这次没有块列表、没有红笔统计，也没有生成任何题卡（入库归 #9/#15） |
| `already_in_inbox` | `hint` | 同一内容已经收过（文件名取内容哈希，同步盘会把同一张再同步一遍） | `<原名>` 的内容收件目录里已经有了（`<stored_as>`），没有重复写一份 |
| `unexpected_file_type` | `warning` | 后缀不在已知照片后缀里 | `<原名>` 的后缀不是已知的照片后缀（…）→ 收下了，但请确认这是照片 |
| `inbox_created` | `hint` | 扫描时收件目录原来不存在、刚建了一个 | 收件目录原来不存在，刚建了一个：`<路径>` |

**页级对账码表（B2 / #10 落地）**——出现在页资源的对账结论里（`server/segmentation.py`
的 `reconcile`、`classify_resegment` 与 `parse_candidate_blocks` 返回的就是 §2 那个 `Warning`：
`{code, message, id, level}`）。页级警告（这一条不对应某一题）的 `id` 为 `null`；
卡级警告（`resegment_card_human_work`）的 `id` 填**那张卡的 id**，界面不必去 grep 消息。
构造只有一处实现（`server/warnings.py: _warn`）——手搓 dict 漏掉 `level` 是踩过的坑。
`level` **显式发出来**：`warning` = 真矛盾；`hint` = 「我这一条没查全 / 我排除了什么」。
把「按设计如此」报成 `warning` 会训练人忽略体检（`proto/server.py:1046-1047`），那比漏报更糟。
`pages.rebind` 交回来的是**事实**（码 + 消息，没有级别）：出口按本表补级别，
表里没有的码按**最响**的那一级（`warning`）报，不许安静降级。

| `code` | `level` | 触发 |
|---|---|---|
| `question_number_gap` | `warning` | 模型报出的题号不连续（有 17、19 却没有 18）。**最便宜也最强的漏题探测器** |
| `question_number_duplicate` | `warning` | 两块报同一个题号（多半是切重了） |
| `question_number_missing` | `hint` | 有块没报出可用的整数题号 → 题号连续性这一条**查不全**（已知弱点，见模块 docstring），不许当成通过 |
| `block_overlap` | `warning` | 同页两个块的边界相交（贴边不算，交集面积为 0） |
| `block_without_box` | `warning` | 块的 `bbox_norm` 缺/退化 → 重叠与覆盖率判据**对它没查**（与 `pages.rebind` 同一个码、同一件事） |
| `block_not_an_object` | `warning` | 块列表里混进了不是对象的项（与 `pages.rebind` 同一个码） |
| `block_removed_with_card` | `warning` | 旧块在新切分里找不到位置重合的块，却绑着卡片（`pages.rebind`；#9 已落地） |
| `page_ink_uncovered` | `warning` | 大片墨迹（≥ `COVERED_MIN`/`UNCOVERED_MIN_PX` 门槛）没被任何块覆盖 |
| `page_ink_draft_excluded` | `hint` | 按「整页草稿式手写」排除了墨迹（**启发式**）；排除了哪些在 `checks.coverage.excluded` 里 |
| `page_ink_invalid` | `warning` | 墨迹区域读不出来（`bbox_norm` 缺/退化、`px` 不是非负数）→ 覆盖率对它没查 |
| `coverage_not_checked` | `hint` | 没有墨迹统计可喂 → 覆盖率这一条**没查**（`checked: false`），不冒充通过 |
| `block_candidate_rejected` | `warning` | 模型报的候选块被拒（`bbox_norm` 读不出来）→ **少了一块**，别当成「这一页就这么多题」 |
| `block_box_clamped` | `warning` | 模型报的框越出页面，裁到页内（原始值写进 `message`） |
| `page_segmentation_unparsed` | `warning` | 模型输出解析不出块列表 → 这是「切分没跑成」，**不是**「这一页没有题」 |
| `resegment_card_human_work` | `warning` | 重切碰到人动过的卡（审核过的字段／人工掩膜／重做历史）→ 只给对照，不改写它 |
| `resegment_candidate_has_binding` | `hint` | 候选块上带着绑定 → 重切不认它，但要说出来 |

**手动修正码表（B6 / #14 落地）**——出现在 `PATCH /api/page/<id>` 与 `POST …/resegment` 的 `warnings` 里。**级别只有服务能定**：真矛盾 → `warning`，「这一步有歧义 / 我替你做了个决定」→ `hint`（把按设计如此的事报成 warning 会训练人忽略体检）。

| `code` | `level` | 触发 |
|---|---|---|
| `page_block_unknown` | `warning` | 点名要改的块不在这一页上 → 那几块一个字节没动 |
| `page_block_box_unusable` | `warning` | 新边界读不出来（形状同 #9 的 `block_without_box`）→ 这一块没动（`add` 那条路上它是「这一块没新增」，同一个码、同一件事） |
| `page_block_box_changed` | `hint` | 边界真的动了；`bbox_px`（加了 pad 的盘上原值）已作废置 `null` |
| `page_block_edit_noop` | `hint` | 这一次改的成本来就是这个样子（重复提交幂等）→ **说出来**，不许显示成成功 |
| `page_merge_needs_two_blocks` | `warning` | 合并至少两块，给少了 → 页文件没动 |
| `page_merge_type_conflict` | `hint` | 合并的两块题型不同 → 取第一块的（合并后是同一道题） |
| `page_blocks_merged` | `hint` | 合并成了（记下并了谁、新边界） |
| `page_split_needs_two_boxes` | `hint` | 拆分至少要两个读得出来的框 → 页文件没动 |
| `page_split_question_no_unset` | `hint` | 拆出来的块还没给题号 → **不猜、不自动编号**（题号连续性检查靠它） |
| `page_split_binding_not_carried` | `hint` | 卡片绑定只留在第一块；其余块还没入库 |
| `page_block_split` | `hint` | 拆成了（记下拆成几块；第一块保留原块 id 与绑定） |
| `page_drop_with_card_binding` | `warning` | 丢弃/合并的块上还绑着卡 → **卡不会被自动删**（删卡是另一件事，归 #15） |
| `page_question_no_invalid` | `warning` | 题号不是正整数 → 不许 `int()` 硬转（那是编一个号，最强的检查会失灵） |
| `page_type_unknown` | `warning` | 题型不在枚举里 → 拼错的值会**静默**让下游判据失效 |
| `page_edit_unknown_action` | `warning` | 不认识的修正动作（HTTP 层是 400，带 `details.allowed`） |
| `page_edit_not_an_object` | `warning` | 修正项不是对象 |
| `page_edit_empty` | `hint` | 这次请求里一条修正都没有 → 页文件没动 |
| `block_not_an_object` | `warning` | 块列表里混进了非对象项 → 点名报出来（与 #9/#10 同码同形状），不静默 filter |
| `mask_box_clamped` | `hint` | 掩膜框越出它所属的块 → 已裁到块内（裁到没面积的框没有画出来） |
| `segmentation_not_implemented` | `warning` | 重切时服务没接上切分那一层 → `blocks` 给 `null`（**不是 `[]`**）。与 #13 的收件管道**同一个码** |

**页资源「建」「入库」与审计码表（B7 / #15 落地）**——出现在 `POST /api/page`、
`POST /api/page/<id>/commit` 的 `warnings` 里，以及 `python3 -m server.audit` 的
`findings[]`（审计的每条发现还带 `check`（哪一项查出来的）、`scope` 与能定位的指针）。

| `code` | `level` | 触发 |
|---|---|---|
| `page_already_exists` | `hint` | 同一张照片（内容哈希前 12 位）已经建过页 → 不重跑切分、不覆盖块列表（块可能被人改过） |
| `page_file_unreadable` | `warning` | 页文件在却读不了 → **不覆盖**、也不写照片（先留证据） |
| `page_commit_nothing_kept` | `hint` | 这一页没有一块「收」的块 → 这次没有生成任何题卡（补收入口在 `server.intake.include_blocks` / CLI `--include`） |
| `page_commit_blocks_skipped` | `hint` | 另有 N 块没入库（`keep` 为 false／null）→ 没有为它们建卡，理由在页文件的 `decision` 里 |
| `page_commit_block_without_box` | `warning` | 记着收、但 `bbox_norm` 读不出可用的整页归一化框 → **没有建卡、也没有分配 id**（否则会生成一张定位不到的卡），先修边界 |
| `page_commit_card_unreadable` | `warning` | 块绑的卡片在盘上却读不了 → **不覆盖**（先留证据），这张卡没有重新生成 |
| `page_photo_missing` | `warning` | 页指向的整页照片不在（**同一个事实、同一个码**：#9 的回填、#15 的入库与审计都用它）→ 入库的卡指向一张取不到的页图（不是拒绝：页与块都还在）；审计说「它是最后的底，不该删」 |
| `card_file_unreadable` | `warning` | （审计）题卡文件读不了 → 点名报出来，不是安静少一张 |
| `page_card_missing` | `warning` | （审计）页里记着收（`keep=true`）又绑了卡，但那张卡不在盘上 → 页说它进库了、盘上没有它（重跑一次入库可以补建） |
| `page_block_not_committed` | `hint` | （审计）收了的块还没有绑定 = 还没入库（正常中间态，说出来而不是当成问题） |
| `card_block_not_kept` | `hint` | （审计）卡片绑的块被标成不收 → **卡不会被自动删**，删不删由人定 |
| `card_bound_to_other_page` | `warning` | （审计）卡片自己的 `source.page_image` 指的是**另一页**，却被绑在这一页上 → 页↔卡对不上账 |
| `card_page_image_missing` | `hint` | （审计）卡片绑在页上，但它自己的 `source.page_image` 空/推不出页 id → 来源信息不全（那条检查能抓住的只有它判据覆盖的部分） |
| `duplicate_transcript_on_page` | `warning` | （审计）**同一页**两张卡的题干逐字相同 → 判据与索引级 `duplicate_transcript` **共用一份实现**（`warnings.duplicate_transcript_groups`：`strip()` 之后 `==`，没有模糊比、没有相似度），只是把范围收到这一页；跨页的重复仍由索引那条报 |

审计还有一档只出现在 `card_self_check`／`card_page_binding` 两项里、但码与级别都沿用别处：
卡片字段自检的码（§8 逐卡表）、页绑定两级（`page_binding_missing` = hint、
`page_binding_lost` = warning，**由 `pages.page_binding` 一处定**）。
审计的退出码**只认 `warning` 级发现**：hint 不算问题（把按设计如此的事报成问题，
会训练人忽略体检）。

**「替换」不是一个可以自动产生的状态**：`classify_resegment` 的 `summary.replaced`
恒为 0——配上的块继承旧绑定（保留），配不上的块本来就没有绑定（新增），
没有第三条路径能让「同一个位置换一张卡」成为重切的自动结果（有会红的测试钉住）。

**收入决策码表（B4 / #12 落地）**——出现在 `run_intake` 与 `plan_decisions` 的
`warnings` 里（页级警告的 `id` 为 `null`，卡/块级警告的 `id` 填**块 id**）。

| `code` | `level` | 触发 |
|---|---|---|
| `intake_page_image_missing` | `warning` | 整页照片不在 → 这一页**每一块**的红笔统计都做不了，全部待定。**不许当成「没有红笔」** |
| `intake_page_image_unreadable` | `warning` | 整页照片读不了（不是 PNG／损坏）→ 同上：全部待定 |
| `intake_block_ink_unknown` | `warning` | 某一块没有可用的红笔统计（块没有 `bbox_px`、或整页统计没跑到它）→ 该块待定 |
| `intake_ink_semantics_conflict` | `warning` | 统计说「有红笔」（`colored_px` 有值）但模型说这一块**看不到红笔**（`none`）→ 两层对不上，按「判不准 → 收」处理并把像素数写进 `message` |
| `intake_semantics_fallback` | `hint` | 这一块有红笔但**语义这一条没查到**：没问模型（预演／没注入抽取角色）、答案丢了、答案不是可读对象、或模型给的语义**不在枚举里** → 按兜底规则落「收」，`message` 说清是哪一种 |
| `intake_semantics_unparsed` | `warning` | 模型答了话但抠不出可用语义（`parsed: false`）→ 按「判不准 → 收」处理。**这是一次回答，不是调用失败**（调用失败是 §9 的 `model_unavailable`，且不留任何决策）。级别跟 #5 的 `judge_output_unparsed` 对齐：答非所问是**真异常** |
| `intake_human_decision_preserved` | `hint` | 这一块人去留已定过（`source: "human"`）→ 自动决策不覆盖它，`message` 说明保留了哪一条 |
| `intake_block_unknown` | `warning` | 一键补收点名要动的块 id 在这一页上找不到 → 那几块**没被动过**（说出来，别让人以为补收成功了） |

**级别按 §8 的两级纪律**：`warning` = 真矛盾（统计说没红笔却调不出语义、模型答非所问、
点了不存在的块 id）；`hint` = 「我这一条没查全」（没问模型、枚举外取值、人定过的被跳过）。
把「没查到」报成 `warning` 会训练人忽略体检（`proto/server.py:1046-1047`）——那比漏报更糟。

顺带记账：#5 的 `judge_output_unparsed`（`level: "warning"`，`server/attempt.py:102`，
「模型答非所问」那一档）此前没进任何码表，本版一并登记——同一个形状的两处不该一处有一处没有。

**写盘路径的纵深防御（数据安全修复 / #12）**——页 id 是**文件名**，页里的 `id`/`image`
是**内容**：内容永远不决定写到哪、读到哪。判据只有一份（`server/pages.py` 的
`page_identity_ok` / `is_page_image_name`），三处入口共用：`save_page`（写之前）、
`intake._load_page`（点名之后）、`backfill_pages`（回填之前）。

| `code` | 形状 | 触发 |
|---|---|---|
| `page_id_mismatch` | **400** `bad_request`（`details.param == "page_id"`）；回填报告里是 `warning` | 页里的 `id` 与目标页 id（＝文件名主干）不逐字相同，或形状过不了 `is_page_id` → **一个字节都不写**。按内容里的 id 拼路径会静默写到别的文件上（`../problems/p-xxx` 会整份覆盖一张真题卡） |
| `page_image_unsafe` | **400** `bad_request`（`details.param == "image"`）；回填报告里是 `warning` | 页里的 `image` 含 `/`、`\` 或 `..` → 不拿它拼 `image_path`（否则会去 `pages/` 外面**读**文件） |
| `filesystem_error` | **500** `internal_error`（`reason`），`message` 带异常类名 | CLI 的 `main` 捕获 `OSError`（含 `FileNotFoundError`）→ D1 信封，**不许裸回溯**；写盘是「先写临时文件再原子替换」，失败不留半个页 |

`save_page(catalog, page, *, page_id, apply=…)` 的写盘路径**只由 `page_id` 决定**；
身份闸在**算路径之前**，`apply=False` 的预演走**同一道**闸——预演报出的 `page_path`
不许是假的（它与真写不许分叉）。

**「另有 M 道没有红笔痕迹、未入库」这句话由服务给**（与 §6.1 第 5 条同一条规矩）：
`not_kept` 报告里 `message` 是服务写好的整句，`by_rule` 逐条列出**可枚举**的理由
（`rule` / `count` / `ids` / `message`），`one_click.entry` 给出**一键补收的可调用入口**。
M 必须**每页都报**（M＝0 也报），绝不许静默丢题——这是 spec #2「已知的漏收」那几条
（老师只写分数不写订正／学生用蓝笔订正／红笔勾表示做对）唯一的补救面。

**跳过码表（出现在 `skipped` 里，不是 `warnings`）**——这一档比警告重：**记录根本没建出来**。

| `code` | 触发 | `message` |
|---|---|---|
| `problem_file_unreadable` | 文件读不了（JSON 解析失败、权限、编码） | `<路径> 读不了：<异常类名>: <说明>` |
| `problem_not_dict` | 文件里不是 JSON 对象 | `<路径> 不是一个 JSON 对象` |
| `problem_missing_field` | 缺 `problem` **对象**（B7 / #15 起语义收窄：题面还没转录**不再**跳过，见下） | `<路径> 缺 problem 对象（题面转录、题型、选项都在里面），建不出这条记录` |
| `inbox_part_empty` | 一次上传里某个 part 是空文件（#13） | 第 N 个 part（`<原名>`）是空的，没有收进收件目录 |

`skipped` 非空时 `stats.problems_skipped` 也非零，且 `count` **不含**被跳过的那些
——「少了一张卡」必须是一个看得见的数字，不是一个安静的空位。

⚠ **B7 / #15 收窄了这一档**：以前「缺 `problem.transcript`」会让整张卡进 `skipped`
（`count` 里看不见它）。而 #15 的**入库建的是骨架卡**——块上只有边界与题号，
题面要等抽取角色或审核时填；静默跳过它就是「入库了却看不见」，与 spec #2 第 17 条
「入库后直接进审核队列」直接冲突。现在有 `problem` 对象、只差转录的卡**收录进清单**
并报 `problem_transcript_missing`（`warning`，由 `warnings.card_warnings` 一处产出，
索引／审计／`POST /api/attempt` 的 422 信封三处自动一致）。

## 9. 错误码表（v0）

| `code` | HTTP | 场景 |
|---|---|---|
| `bad_request` | 400 | 路径参数、查询参数、body 不合法。`details` 里必须点名**是哪个参数、值是什么、允许什么**，`hint` 里给**规则本身**（例如 id 的字符集）。**输入错不许用 404 或 500 表达** |
| `not_found` | 404 | 没有这条路由 / 没有这道题 / 没有这张图。路由级 404 的 `message` 会指出**这条路径像哪条已知路由**；命中 §10 的预留命名空间时另带 `details.reserved`，明说「预留、还没实现」——含糊的 404 会让人以为是打错了字 |
| `method_not_allowed` | 405 | 端点收到它不接受的方法（只读端点收到 POST、`/api/inbox*` 收到 GET…）。`details.allowed` 列出允许的方法，**`OPTIONS` 永远在列表里**（预检） |
| `internal_error` | 500 | 服务自己出错。`message` 带异常类名，`hint` 指向服务日志。**不允许用 500 表达「输入不对」** |
| `payload_too_large` | **413** | 一次上传的 body 超过上限（默认 32MB，见 §10.3）。`details` 给 `{param, value, max}`。**判在读 body 之前**：上限要在声明长度上就判掉，读一个百 MB 的 body 再拒绝不是拒绝。这个响应之后连接会关闭（body 没读完，keep-alive 会串味） |
| `not_auto_judgeable` | **422** | **#5，已实现**：`reason` ∈ §6 的三个码之一，`message` 就是那句中文原话，`warnings[]` 带该卡的自检警告。**不是 400，更不是 500**（编排裁决 D1） |
| `model_unavailable` | **502** | **#5，已实现**：判定角色调用失败（网络／超时／缺密钥）。同一个信封，`reason = "model_unavailable"`。**这一次重做不留下任何记录**——「我们没能问成」不是「看不清」，记成看不清会凭空造出一条没发生过的重做，并静默重置冷却。界面可以直接重试。**本版（前端重构 / #17）起简报生成与切分也走同一个形状**（§10.5、§10.2.1b） |
| `ambiguous_attempt_at` | **409** | **#6，已实现**：定点修正的 `attempt_at` 定位到**不止一次**重做（同一秒里做了两次）。`details.candidates` 列出命中的索引。**不是 404**：那几次确实存在，只是这个参数区分不了它们；挑一个就是「猜」 |
| `resegment_needs_confirmation` | **409** | **本版（前端重构 / #17）新增**：`POST /api/page/<id>/resegment` 是**重置为预设**（破坏性），这一页存在人工改动而请求没带 `{"confirm_discard_manual": true}`。`details.discarded` = `{manual_blocks, removed_blocks, mode_before}`（将丢掉什么：**两个数得准的计数** ＋ 重置前那份块列表的来源，理由见 §10.2.1b）。**不是 400**：参数没写错，是这次操作会毁掉人的劳动；与 `ambiguous_attempt_at` 同一档——**不许替人做不可逆的决定**（§10.2.1b） |
| `brief_unverifiable` | **502** | **本版（前端重构 / #17）新增**：简报的**数字闸门**没过——生成出来的每一个数字都必须在**本次索引**里逐字找回，任何一条对不上**这份简报就不落盘**。`details.facts` 列出对不上的那些（`{label, path, claimed, actual, reason}`；`reason` ∈ `path_unresolved` / `value_mismatch` / `kind_mismatch` / `missing_value` / `bad_fact` / `missing_fact`，取值与理由见 §10.5——**`actual = null` 同时覆盖「解不出来」与「索引里正好是 null」，所以必须靠 `reason` 分开**）。**它与 `model_unavailable` 是两个码，不许合**：处置完全不同——模型没问成**可以直接重试**，数字对不上**重试无用**（提示词或索引没变，重试只会再编一次），合成一句「可以重试」会把人引去重试一个不会变好的东西。理由：这个项目最怕的失败是一段读起来很顺、**数字却是编的**总结，而能自动判的只有「数字对不对」（§10.5） |

`not_found` 下的 `reason` 取值（都是 404，`code` 不变）：没有这道题（`reason == "not_found"`）、
没有**那一次重做**（`reason == "attempt_not_found"`，#6，`details.available` 列出实际有哪些时刻）、
没有这张图（`reason == "not_found"`）、
这个科目**还没有简报**（`reason == "brief_missing"`，本版（前端重构 / #17）新增；`?at=` 要的那一天没有则
`details` 里给出**实际有哪几天**，见 §10.5——**不是**「这个科目不存在」，那个是 400）。

`internal_error` 下的 `reason` 取值（都是 500，`code` 不变）：

- `page_file_unreadable`：页文件在却读不了；
- `filesystem_error`：盘上操作失败，`message` 带异常类名；
- `internal_error`：**兜底**——代码里没有更细的原因时 `reason` 落到 `code` 本身
  （`http.Api.handle` 最后那一档未预期的异常、题卡读不了、上传页文件读不了）。
  **不许用裸回溯代替信封**（D1）：这一档的 `message` 也带异常类名，`hint` 指向服务日志。

`bad_request` 下的 `reason` 取值（都是 400，`code` 不变，`details.param` 点名字段）：
页里的 `id` 与目标页 id 不一致（`page_id_mismatch`）、页里的 `image` 不是纯文件名
（`page_image_unsafe`）——写盘路径只认调用方给的页 id，页里的 `id`/`image` 只用于对账（§8）；
写端点的 body 超过**显式上限**（`body_too_large`，见 §10.1）；
要删的块已经绑了题卡（`block_delete_bound_to_card`，**本版（前端重构 / #17）新增**，
`details` 带 `card_id`——那张卡已经存在，删块会造出孤儿绑定，要「不要它」只能用 `drop`）；
科目不在受控词表里（`subject_unknown`，本版（前端重构 / #17）新增，`details` 给
`{param:"subject", value, allowed}`）。**它与 §8 逐卡表那条警告同名**，因为**是同一个事实**
（这个值不在受控词表里），只是层次不同：这里是**拒绝一次输入**（400），那里是**自检一份已有数据**
（`warning`）。R2/R9 裁过「同一个事实两个码会让界面出现两种说法、让按码统计永远对不上」，
所以名字必须一样；**码表分开是应该的**（错误进 §9、警告进 §8），两处各指认对方。

**写端点 body 的显式上限（64 KiB）**：`POST /api/attempt/*` 的 body 合法形状只有
`{channel, answer}`（或定点修正那三个键），所以上限远小于上传那一档——
没有上限时一个 2MB 的**合法 JSON** 会被整段读进内存、作答还会发给模型。
超限 → **400 `{code:"bad_request", reason:"body_too_large", details:{param:"body", value, max}}`**，
`hint` 指向「照片请走 `POST /api/inbox`」。**判在读 body 之前**（`app.py` 按
`Api.body_limit(path)` 决定读不读），且拒绝**不留下任何记录**（D1/D9）。
上限按路由分档：写端点 64 KiB、上传 32MB——`Api.body_limit` 是唯一判据。

补充硬规则（编排裁决 D1）：

- **拒绝用 422，不用 400**：请求本身是合法的（题存在、字段对），只是这道题**不能**自动判定。
  400 留给「你参数写错了」。
- **绝不允许**裸 500、空响应体、`text/plain` 的异常回溯、`sys.exit`。
  v0 里那唯一一条兜底 500（`internal_error`）也走同一个 JSON 信封，`message` 带异常类名，
  `hint` 明说「输入不合法应该是 400 而不是 500」。

## 10. 预留与**已落地**的写端点

工单 #3 当时「不做任何写端点」，所以形状先钉在这里，免得 #5 #9 #13 各自发明一套。
**现在两份已经落地**（形状仍然以本节为准）：

| 小节 | 状态 |
|---|---|
| §10.1 `POST /api/attempt/<pid>` | **已实现**：屏幕重做（#5）与定点修正（#6）；`channel:"paper"`（人工确认）仍是预留 |
| §10.2 `/api/page*` | **已实现**（六个动作都有路由）：「读」（`GET`；**本版补上**——界面打开一页来改，第一步是**读**）、「改」（`PATCH`，#14；**本版（前端重构 / #17）加了 `add`／`delete` 两条动作、回执加 `blocks_removed`／`blocks_added`**）、「重切」（`POST …/resegment`，#10/#14；**本版语义改成「重置为预设」，要显式确认**）、`GET …/image`（画块框要整页照片，#14）、「建」（`POST /api/page`，#15；**本版起切分不可用也建页，并收可选字段 `subject`**）、「入库」（`POST …/commit`，#15）。页文件的块已长出 `question_no`（#10）／`ink`（#11）／`decision`（#12）／`problem_type`（#14）／`card_id`（#15 的入库写入）；页文件本身长出 `subject`／`segmentation`／`removed_blocks`（本版） |
| §10.3 收件目录与手机上传页 | **已实现**（#13） |
| §10.4 受控词表与大纲（**数据文件，不是端点**） | **本版（前端重构 / #17）新增**：`<数据目录>/vocab/subjects.json` 与改形后的 `<数据目录>/vocab/topic-outline.seed.json` 的形状、存量回填命令 `python3 -m server.subject_assign`，以及它们怎么进 `/api/index`（§3） |
| §10.5 `/api/brief/<科目>` | **本版（前端重构 / #17）新增**（形状先钉住）：读最近一份简报、按需生成一份（走 `brief` 角色，过数字闸门） |

### 10.1 `POST /api/attempt/<pid>` —— #5、#6（**两种形态已实现**）

三种形态：

```jsonc
{ "channel": "screen", "answer": "<作答文本>" }                 // 自动判定（#5，已实现）
{ "channel": "paper", "verdict": "…", "at": "…", "error_causes": ["…"] }  // 人工确认（预留）
{ "attempt_at": "…", "error_causes": ["…"], "verdict": "…" }     // 定点修正（#6，已实现）
```

形态由**键**分辨：带 `attempt_at` 的一律走定点修正。

| 结果 | HTTP | `error` |
|---|---|---|
| 成功 | 200 | `data: {attempt: {…完整那一次…}, mastery: {…见下…}, run_id: "…", index_rebuilt_at: "…"}` |
| 三个拒绝理由 | **422** | `{code:"not_auto_judgeable", reason:"solution_type"\|"no_standard_answer"\|"unreviewed", message:"<autojudge 那句原话>", details:{id}, }` + `warnings[]` = 该卡的自检警告 |
| 模型调用失败 | **502** | `{code:"model_unavailable", reason:"model_unavailable", message:"…", hint:"可以直接重试；这一次没有留下任何记录"}` |
| `channel`／`verdict` 取值非法 | 400 | `{code:"bad_request", details:{param,value,allowed}}` |
| body 超过 **64 KiB** 上限 | 400 | `{code:"bad_request", reason:"body_too_large", details:{param:"body", value, max:65536}}`。**判在读 body 之前**，拒绝不留记录（D1/D9）；照片走 `POST /api/inbox`（那一档上限 32MB，超限是 413） |
| 定点修正：`attempt_at` **不存在** | **404** | `{code:"not_found", reason:"attempt_not_found", message:"…", details:{id, attempt_at, available:[…]}}` + `warnings[]` = 该卡的自检警告。`available` 是这道题**实际有哪些**重做时刻。**绝不落到最近一次、也绝不按「最接近」匹配** |
| 定点修正：`attempt_at` **定位到不止一次** | **409** | `{code:"ambiguous_attempt_at", reason:"ambiguous_attempt_at", details:{id, attempt_at, candidates:[索引…]}}`。同一秒里有两次重做时不许静默挑一个 |

`reason` 与 `message` **必须**取自 `server/autojudge.py` 的同一份实现（§6）——
#5 的派发简报已经写明「必须调用它、不许重写」。

**这一次重做记成什么形状**（`attempt`，字段与 `Problem.attempts_detail` 的元素同一个形状）：

```jsonc
{
  "at": "2026-10-04T09:39:57+00:00",   // 这一次重做的时刻（定点修正靠它定位，不靠「最近一次」）
  "channel": "screen",                 // 枚举只有两个值：screen（屏幕重做）｜ paper（纸上重做）
  "verdict": "correct",                // correct（对）｜ wrong（错）｜ unreadable（看不清）
  "source": "auto",                    // auto（自动判定）｜ human（人工确认）；改判后变 human
  "confidence": 0.93,                  // 模型给的把握；人工确认时为 null
  "provider": "deepseek",              // 谁判的（裁决 D2：与 model 是两个字段）
  "model": "deepseek-flash",
  "error_causes": ["计算失误"],
  "note": "判对且脱离冷却：连续正确 1/2",
  "overrode": { "verdict": "wrong", "source": "auto", "confidence": 0.6,
                "provider": "deepseek", "model": "deepseek-flash" }  // 只在改判后出现
}
```

**定点修正（#6）只改既有那一次重做**：不新建记录、不重跑判定、不调模型
（`run_id` 恒为 `null`），也不看这道题能不能自动判定（人给的判定不需要闸门）。
`verdict` 与 `error_causes` 至少要给一个；两个都不给 → **400**（什么都没得改，
不假装成功）。除这两个字段外的键一律 400：`source`／`confidence`／`provider`／`model`／
`overrode` 是**服务**写的审计字段。

改判（给了 `verdict` 且与现值不同）时：

- `source` → `human`，`confidence`／`provider`／`model` → `null`（契约把它们定义为
  「谁判的」，改判后判的是人）；
- `overrode` 记下**最初**那次原判定 `{verdict, source, confidence, provider, model}`
  ——前三个键是 §10.1 的原形状，后两个是超集，否则「原本是哪台机器误判的」永久丢失
  （spec #1 US 28）。**只改错因不算改判**：不写 `overrode`，来源与置信度一概不动。
- 同一 payload 重复提交**幂等**：不追加记录、不改状态、`overrode` 不覆盖（验收 3）。

**掌握按「判据变没变」分流**（#6 编排裁决 D10）：

- **改判**（给了 `verdict` 且与现值**不同**）→ **重放重算**（`server/mastery.recompute_mastery`）：
  改的是历史里某一次（甚至不是最后一次）时，把卡里 `attempts` 从头按既定规则**重放**一遍。
  状态机对「`created_at` + 一串 (at, verdict)」是确定的纯函数，所以重放得到的读数**就是**
  「这条判定从一开始就是这样」时该有的读数；就地打补丁会让后面几次的 `credited`
  仍按旧判定算。重放用的是**同一个冷却门**（先算门、后写 `last_attempt_at`，见下），
  不允许定点修正另写一套。
- **只改错因**（没给 `verdict`，或给的与现值**相同**）→ **一个字都不重算**：卡上存的
  `mastery` 与既有 `attempts[i].note` **逐字保留**，只写 `error_causes`。纯标注编辑不该销毁
  人动过的状态（spec #2 的同一原则：「人动过的部分不许被一次无关操作抹掉」），更不该静默地改
  （ADR 0007 第 6 条）；「卡上状态与历史不一致」这种漂移该被审计报出来（#15），
  而不是被一次无关编辑顺手改掉。

于是定点修正返回的 `mastery` **分两层**（改的是最后一次时两者重合）：

- `state`／`streak`／`last_attempt_at` = **卡级终态**（重放后与 `GET /api/index` 一致；
  没重放时就是卡上原样存的那三个）；
- `cooling`／`credited`／`gap_days` = **被改那一次**的读数，`note` = 那一次记录上的原话
  （契约：`attempt.note` 与 `mastery.note` 是同一句）。这几个读数由 `mastery.peek_step`
  **只读地**看（与重放共用同一份冷却门、同一条 `credited` 规则），所以「改判」与「只改错因」
  两条路径给出的读数**逐字一致**——同一 payload 重发一次（第一次改判、第二次已是同一个
  verdict）幂等的证据正是这个。

**`mastery` 是这次重做之后的读数**（形状与 `Problem.mastery` 一致，另加两个解释用的字段）：

```jsonc
{
  "state": "in_pool",       // in_pool（在池）｜ graduated（毕业）
  "streak": 1,              // 连续正确次数
  "last_attempt_at": "2026-10-04T09:39:57+00:00",
  "cooling": false,         // **这次重做发生时**是否处于冷却窗口：判定门在写入 `last_attempt_at` **之前**取（顺序反了会让当天判对计入掌握）。`cooling:true` → `credited:false`（只热身）；判错**也**进冷却，那是设计不是 bug
  "credited": true,         // **这一次**算不算进连续正确。false = 只是热身（判对但在冷却期内）
  "gap_days": 8,            // 距上一次重做几天（从未重做过的以录入时间起算）
  "note": "判对且脱离冷却：连续正确 1/2"   // 给人看的原话，界面照它显示
}
```

#7 的两句读数就长在这里（spec #1 的故事 8/9）：

- **「已计入 1/2」** = `mastery.credited == true` 时的 `streak` / 2（掌握 = 连续 2 次）。
- **「只热身、不计入掌握」** = `mastery.credited == false`（判对但仍在冷却期），
  此时 `streak` 不动，`note` 里也有这句原话。

**留档（`runs/`）**：#5 验收第 3 条要求每次自动判定留一份调用档（提示词与用量）。
`run_id` 就是那份档的标识，200 时一并返回；留档落在**数据目录**下的 `runs/`
（`server/paths.py`，[ADR 0008](../adr/0008-runtime-files-live-in-the-user-data-dir.md)），不进仓库。
人工确认不调模型，**不留档**，`run_id` 为 `null`。

### 10.2 页资源 —— 归属 #9 #10 #12 #14（编排裁决 D5）

`/api/page*` 命名空间预留。页是**一等实体**（CONTEXT「页」）：

- **一页一文件**：`data/pages/<hash12>.json`，`<hash12>` 取自 `source.page_image` 的
  文件名（如 `41c86bcfc007`），与照片**同目录并列**。**不要**从题卡 id（6 位）推。
- **块边界的规范基准是归一化坐标**（整页 `bbox_norm` 语义）；`bbox_px` 只作交叉验证。
  归一化坐标不随图片重编码／缩放失效。
- 四个动作的**位置**（形状由 B 系列工单定，届时补进契约 v1）：
  `POST /api/page`（建：照片进 → 存图 + 建页文件 + 跑切分 → 返回块列表）、
  `PATCH /api/page/<id>`（改：边界／合并／拆分／去留／题型／题号）、
  `POST /api/page/<id>/resegment`（重切：返回逐块对照，**不写题卡**）、
  `POST /api/page/<id>/commit`（入库：生成题卡并回写绑定）。

> ⚠ **两套坐标基准，不许混用**：`Problem.source.bbox_*` 是**整页**坐标，而
> `Problem.clean.boxes_norm`／`manual` 是**裁剪图**坐标。任何「把块画到原图上」或
> 「把掩膜框画到页上」的地方（尤其 #14）**必须显式换算**。这是本轮登记在案的一处坑。

#### 10.2.1 页文件形状（**B1 落地**，= `server/pages.py`）

```jsonc
// data/pages/41c86bcfc007.json
{
  "version": 1,
  "id": "41c86bcfc007",            // = 文件名主干 = 整页照片的文件名主干
  "image": "41c86bcfc007.png",     // 整页照片的**文件名**（与页文件同目录并列）
  "created_at": "2026-10-04T14:31:35+08:00",
  "subject": "数学",               // 本版（前端重构 / #17）新增：与题卡上的 subject **同名同义**；null = 未归类
  "origin": {                      // 来源信息（spec #2：「第几页、哪张卷子」）
    "original_file": "2.png",      // 上传时的原始文件名（来自 source.original_file）
    "sheet": null,                 // 哪张卷子；未填为 null
    "page_number": null            // 第几页；未填为 null
  },
  "segmentation": {                // 本版（前端重构 / #17）新增：当前这份块列表是从哪来的
    "mode": "model",               // model（机器切出来的**预设**）｜manual（人画的，含在预设上增删改之后）｜unavailable（还没有块列表，等人来画）
    "at": "2026-10-04T14:31:35+08:00",  // 这份块列表是什么时候定下来的；unavailable 时为 null
    "note": null                   // 一句话说明（例如「切分不可用：那一层没接上」）；没有为 null
  },
  "blocks": [
    {
      "id": "b1",                                   // 页内唯一、稳定的块 id
      "bbox_norm": [0.02, 0.12, 0.76, 0.28],        // 规范基准：整页归一化 xywh
      "bbox_px": [3, 20, 541, 79],                  // 只作交叉验证：整页像素 xyxy
      "card_id": "p-20261004-41c86b",              // 绑定；null = 还没入库
      "keep": true,                                  // 去留：true 收 / false 丢弃 / null 待定
      "ink": {                                       // #11 的 ink_statistics 原样；null = 统计读不出来
        "area": 4108, "colored_px": 210, "colored_ratio": 0.05,
        "dark_px": 1100, "dark_ratio": 0.27
      },
      "decision": {                                  // #12：为什么收／为什么没收（审计面）
        "keep": true,                                // 与块的 keep 同一份结论（冗余存一份，读一条就够）
        "rule": "error_trace",                       // 命中的规则码，见下面那张表
        "source": "model",                           // model｜fallback｜statistics｜human
        "semantics": "correction",                   // 模型回答的语义；没人判过为 null
        "reason": "红笔是订正（改错）（表示这道题错了）→ 收：有表示「错」的痕迹",
        "confidence": 0.93, "provider": "deepseek", "model": "deepseek-flash",
        "run_id": "20261004-190000-000-redpen-semantics.json",
        "at": "2026-10-04T19:00:00+08:00"
      }
    }
  ],
  "removed_blocks": [              // 本版（前端重构 / #17）新增：**被删掉的块**的留痕
    {
      "block_id": "b3",            // 删掉的那一块原来的 id
      "bbox_norm": [0.05, 0.62, 0.9, 0.18],  // 它原来在哪（整页归一化 xywh）
      "question_no": 7,            // 它原来的题号；没给过为 null
      "removed_at": "2026-10-04T15:02:00+08:00"
    }
  ]
}
```

**`subject`、`segmentation` 与 `removed_blocks`（本版（前端重构 / #17）新增）**：

| 键 | 形状 | 含义 |
|---|---|---|
| `subject` | str \| null | **与题卡上的 `subject` 同名同义**（§3.1）：这一页的科目，取自受控词表；`null` = **未归类**。它在**录入时**由人指定（`POST /api/page` 的可选字段，见 §10.2.1b），入库时原样印到这一页生成的卡上——同一件事只问一次（不在这里现场重判一遍） |
| `segmentation` | `{"mode": "model" \| "manual" \| "unavailable", "at": ISO \| null, "note": str \| null}` | `mode` 记的是**当前这份块列表的来源**：`model` = 机器切出来的**预设**；`manual` = 人画的（含在预设上增删改之后）；`unavailable` = **还没有块列表，等人来画** |
| `removed_blocks` | `[{block_id, bbox_norm, question_no, removed_at}]` | **被删掉的块**的留痕（`delete` 动作与「重置为预设」往这里追加，见 §10.2.1b）。删掉的块**必须**在这里，**绝不静默消失** |

四条硬规矩：

1. **`blocks: null` 与 `[]` 的既有语义一个字都没变**（#13 定的那一条）：`null` = 还没切／
   没有列表，`[]` = 确实切出 0 块。`segmentation.mode` 是把「这块列表是谁定的」**说出来**，
   不是替代它。`mode == "unavailable"` 时 `blocks` **必须是 `null`**——
   「不知道」不许被写成 `[]`（那看起来像「这一页没有题」）。
2. **`removed_blocks[]` 里永远不许出现带 `card_id` 的块**。这一条是硬的：**已绑 `card_id` 的块
   永远不许删**（§10.2.1b 的 `delete`），所以留痕里出现 `card_id` 就等于「删块那道闸破了」——
   那是孤儿绑定的直接证据。要「不收它」只能用 `drop`（块留在 `blocks` 里、带
   `decision.rule = "human_drop"`，可审计）。
3. **`removed_blocks[]` 只增不减，同一个 `block_id` 允许多条**：它是**审计账**，
   清账就是删留痕。所以「重置为预设」把同 id 的块建回来时**也保留**旧留痕——
   那一块确实是被人删过一次，这件事没有因为重建而没发生。要「这一块现在在不在」看 `blocks`，
   要「这一块被动过几次」看留痕：两个问题两份读数。
4. **切分不可用时也要建页**（**本版（前端重构 / #17）改动，推翻一条旧裁决**）：照片落盘、
   页文件照建，`segmentation.mode = "unavailable"`、`blocks = null`、`removed_blocks = []`。
   理由两条：①**切分不可用不等于「这一页没有题」**——前者是「我不知道」，后者是事实，
   两件事必须分得开；②人要能在这张页上**手动画框**，而**页文件不建，人就没有东西可画**。
   旧裁决（ADR 0005 / #15 交付时）是「切分不可用 → 页文件不建、照片也不落盘」，
   它让界面无从下手；推翻它这件事记在 §11 的两张表里（#17 §3.6、§11.3）。

**两个边界的形状不一样，别当成同一件事**：

| 字段 | 形状 | 来源 |
|---|---|---|
| `bbox_norm` | `[x, y, w, h]`（**xywh**），相对**整页**归一化、原点左上 | 与 `source.bbox_norm` **同一语义**（`proto/slice.py:281`） |
| `bbox_px` | `[x0, y0, x1, y1]`（**xyxy**），整页像素 | `crop_problem(pad=0.015)` 加 1.5% pad 又裁到页边界的结果（`proto/slice.py:544-555`） |

回填时 `bbox_px` **照抄盘上的原值**，不用 `bbox_norm` 重算。
下游扩展键**先占名字**：`question_no`（#10 落地）、`ink`（#11 定义、#12 写入）、
`decision`（#12 新增）、`type`（#14）；**本版（前端重构 / #17）新增的页级键是三个**：
`subject`、`segmentation` 与 `removed_blocks`（上面那张表）。

**`keep` 与 `decision` 是一对**（#12 验收 3）：`keep` 只说去留，`decision` 说**为什么**。
`decision` 的每个块都要有（`rule` + `reason` + `source` + `semantics`），因为「切分结果与
收入决策一旦只存在于界面的内存里，『漏了一题』就永远查不出来」（spec #2）——
决策可审计是这条规则的唯一落点。**人补收/丢弃过的块（`source: "human"`）重跑自动决策时
原样保留**（人动过的部分不许被抹掉，与 D10 同源）。

`rule` 的取值（stable，改它＝改历史读数）：

| `rule` | 触发 | `keep` |
|---|---|---|
| `error_trace` | 语义是叉／圈错／订正（表示这道题错了） | `true` |
| `tick_only` | 红笔**只是**一个对勾（表示做对了）——唯一的不收例外 | `false` |
| `uncertain_defaults_to_keep` | 判不准：分数、圈题号、看不清、枚举外的值、没拿到语义 | `true` |
| `no_red_ink` | 统计说这块没有红笔（`colored_px <= ink.COLOR_MIN_PIXELS`） | `false` |
| `ink_unknown` | 统计读不出来（块没有可用 `bbox_px`／整页照片不在或读不了） | `null`（待定） |
| `human_include` / `human_drop` | 人补收／人丢弃（`source: "human"`） | `true` / `false` |

「判不准 → 收」是**决策**不是兜底：漏收一道错题它永远不在库里，误收一道对题审核时
删掉几秒（代价不对称）。所以「没有红笔」与「读不出来」必须分成两档——`ink_unknown`
的 `null` 不是「宁可不收」，它是「我还不知道」，并且**每页都必须把它报出来**（§8 码表）。

**题卡上的 `source.{page_image,bbox_norm,bbox_px,original_file}` 保留不动**：
绑定关系只记在页文件里（spec #2 原话），所以「这张卡有没有页绑定」是从页文件推出来的，
卡上不加新字段。

#### 10.2.1b 页资源的 HTTP 形状（**#14 落地其中两个；本版补「读」**）

| 动作 | 路由 | 请求 | 响应 `data` | 状态 |
|---|---|---|---|---|
| 读（**本版补上**） | `GET /api/page/<id>` | 无（纯 GET、无 body） | `{page_id, page_path, image, page}`——`page` 是**页文件原样**（块列表、`segmentation`、`removed_blocks`、`subject` 都在里面）；`image` 是纯文件名，整页照片走 `GET /api/page/<id>/image` | **本版新增** |
| 改 | `PATCH /api/page/<id>` | `{dry_run?: bool, edits: [{action, …}]}` | `{page_id, page_path, apply, preview, changed, blocks_removed, blocks_added, edits[], page, wrote_cards, wrote_page}` | **已实现** |
| 重切（**本版（前端重构 / #17）起语义 = 重置为预设**） | `POST /api/page/<id>/resegment` | `{"confirm_discard_manual": true}`（**本版新增**；没带而这一页又存在人工改动 → **409**） | `{page_id, segmentation, ran, blocks\|null, matches[]\|null, removed[]\|null, discarded, summary\|null, rejected[], wrote_cards, wrote_page, checks, reconciliation}` | **已实现（语义本版改动）** |
| 整页照片 | `GET /api/page/<id>/image` | — | 图片字节（失败仍是 JSON 信封） | **已实现** |
| 建 | `POST /api/page` | `multipart/form-data`，字段名 `file`（与 `POST /api/inbox` 同一形状；一个文件 = 一页）；**可选**文本字段 `subject`（**本版（前端重构 / #17）新增**，三条规矩见下） | `{pages: [{page_id, page_path, image, created, existing, subject, segmentation, blocks\|null, rejected?, message, counts?, not_kept?, checks?, reconciliation?}], created[], existing[], segmentation{available,reason,message}, wrote_pages}`——`pages[].subject` **回显这一页收到的科目**：没给科目就是 `null`（**未归类**，不是缺字段）。回执不回声一件事，界面就没法确认自己提交的科目真的被收下了（「不许静默」） | **已实现（#15，`server/page_create.py`）** |
| 入库 | `POST /api/page/<id>/commit` | 无 | `{page_id, page_path, at, blocks{total,kept,dropped,pending,not_an_object}, created[], reused[], skipped[], refused[], wrote_page, wrote_cards, index{count,card_ids,built_at,warnings,skipped}}` | **已实现（#15，`server/page_commit.py`）** |

**`checks` 与 `reconciliation`：确定性对账进生产路径（R1）**。「建」与「重切」两条路都跑
`segmentation.reconcile` 的三条判据（题号连续性／块重叠／覆盖率），只报不改：

| 键 | 是什么 |
|---|---|
| `checks` | 三条判据各自的**事实**：`{question_numbers, overlaps, coverage}`。§8 说的「排除了哪些在 `checks.coverage.excluded` 里」就是这里；`coverage.checked: false` 表示没有墨迹统计可喂（覆盖率这一条**没查**，不许当成通过） |
| `reconciliation` | 汇总读数：`{blocks, checks_run, checks_skipped, alarms, ok, complete}`。`ok` 只覆盖**查过的**那部分，`complete` 说清有没有判据在瞎着——「查过且通过」与「没查」必须长得不一样 |

覆盖率要整页墨迹区域（`ink.page_ink_regions`：八连通，红笔与深色取并集）。重切那条路读
页文件旁边那张整页照片；读不出来 / 不在 → `ink` 为 `None` → `coverage_not_checked`（hint）。
「建」的 `checks`/`reconciliation` 只在 `segmentation == "ran"` 那一行出现（已存在的页不重跑
切分，也就没有新的对账结论要说）。

**「建」的四条规矩**（#15）：

1. **页 id = 照片内容哈希的前 12 位**（与 #13 收件目录同一套口径）→ 同一张照片再传一次
   就是同一个页 id，报 `page_already_exists`（`hint`）、**不重跑切分、不覆盖块列表**。
2. **模型失败 → 502 且 `data/` 里一个字节都不留**：照片先在**系统临时目录**里跑切分，
   成功之后才写进数据目录（D1/D9「拒绝就该一个字节都不动」——#13 曾经先 `ensure()`
   建目录再判空）。
3. **切分不可用／解析不出块都不是「这一页没有题」**：`blocks` 给 `null`（不是 `[]`），
   报 `segmentation_not_implemented`／`page_segmentation_unparsed`。
   **本版（前端重构 / #17）改动：页文件照建、照片照落盘**（`segmentation.mode = "unavailable"`，
   §10.2.1）——旧裁决是「切分不可用 → 页文件不建、照片也不落盘」，那让界面**无从下手**
   （人手动画框要有页文件可画）；这条推翻与理由记在 §10.2.1 第 3 条与 §11 两张表里。
4. **统计与去留不在这里判**：块上的 `ink` 来自 #11 的 `ink.page_block_reports`，
   建议去留来自 #12 的 `intake.plan_decisions`（这一趟 `semantics` 为空 → 有红笔的块
   按「判不准 → 收」落向收，并报 `intake_semantics_fallback`）。**建只花一次模型调用：
   切分那一次**；红笔语义那一趟是 `python3 -m server.intake --apply`。
   块的 `bbox_px`（整页像素 xyxy）是**新页的初值**，按照片真实像素尺寸推一次
   （回填那条路仍照抄盘上原值，D5）；#14 的拖边界会让它作废置 `null`。

**「建」时的科目 `subject`（本版（前端重构 / #17）新增）**——它写在**页**上（§10.2.1），
入库时原样印到卡上；三条规矩：

1. **没给 / 给了空串 → 未归类**（`subject: null`）。不是错，是一等状态（§3.1）：
   新卡先响 `subject_missing` 的 `hint`，侧栏「未归类」那一栏兜住它。
2. **词表在、而取值不在 → 400** `bad_request`（`reason = "subject_unknown"`，
   `details` 给 `{param:"subject", value, allowed}`，§9）：科目的取值只能来自受控词表
   （`CONTEXT.md`「受控词表」：AI 不得自造标签）。这条判在**碰盘之前**——
   拒绝路径一个字节都不动（D9）。
3. **词表本身不在**（没建 / 读不了 / 是空的）**→ 收下，并把词表级警告一并带回**
   （§8：`subjects_vocab_missing` 等）。理由：**录入摩擦是这类工具的头号死因**
   （ADR 0006 决定第 5 条原话：「录入摩擦最小化是这个项目的生命线」），
   不许因为词表缺席就把录入整个挡住。代价是词表补上之后会有卡带着表外的科目，
   而那正是「索引级词表警告 + 逐卡 `subject_unknown`」要喊的事——**先收下、再喊**，
   不是先拦下。

**「入库」的四条规矩**（#15）：

1. **只给「收」的块发卡号**：`keep` 为 `false`（明确不收）或 `null`（统计读不出来，待定）
   的块进 `skipped[]`，**连 id 都不分配**——给丢弃的块发卡号就是造一条幽灵绑定。
2. **id 首次入库时分配**（`pages.assign_card_ids`，seed = `<页 id>#<块 id>`，
   唯一性在「盘上已有的 id 集合」上做碰撞消解）；**已经生成过的块不重复生成**
   （进 `reused[]`，卡文件逐字节不动）。
3. **先写页、再建卡**（这条顺序是幂等的一部分）：绑定先落盘，中断后重跑走 `reused`
   那条路，id 不会变；反过来先建卡则会因为候选 id 已被占用而碰撞到下一个候选，
   同一块换了 id。半途而废的状态是**看得见**的（`page_card_missing`，warning），
   重跑一次 `commit` 又能补齐。
4. **骨架卡**：`review.status = "unreviewed"`、`standard_answer.value = null`、
   `problem.transcript = ""`、`problem.image`／`clean_image` 是 `null`
   （**不记一个取不到的路径**，否则 `original_image_file_missing` 会误报）。
   卡的 `source` 记 `{page_image, bbox_norm, bbox_px, original_file}`，其中 `page_image`
   是**相对数据目录**的 `pages/<页 id>.<后缀>`（`--data` 可配，所以不写 `data/` 前缀；
   页 id 的推法都只看文件名主干）；**卡上不加新字段**（绑定只记在页文件里）。
   新卡因此在两处被挡住，而这两处读的是**同一份实现**：`server/autojudge.py`
   （未审核 → 不参与自动判定）与 §6.1 的硬闸门（缺擦除图 → 不进屏幕重做）。

`edits` 的 `action` 是**闭集**（spec #2 的最小集合，stable；**本版（前端重构 / #17）加了两条**）：

| `action` | 字段 | 语义 |
|---|---|---|
| `move` | `block_id`, `bbox_norm`（整页 xywh） | 拖边界；`bbox_px` 过期置 `null` |
| `merge` | `block_ids`（≥2） | 合并两块，边界取**并集** |
| `split` | `block_id`, `boxes`（≥2）, `question_numbers?` | 拆分一块 |
| `drop` / `keep` | `block_id` | 整块丢弃／切换收入——**走 #12 的 `set_keep_by_human`**，不另立判断 |
| `type` | `block_id`, `problem_type`（`choice`\|`fillin`\|`solution`\|`null`） | 改题型 |
| `question_no` | `block_id`, `question_no`（正整数或 `null`） | 改题号 |
| `add` （**本版（前端重构 / #17）新增**） | `bbox_norm`（整页 xywh）, `question_no?`（正整数或 `null`）, `problem_type?`（`choice`\|`fillin`\|`solution`\|`null`）, `keep?`（bool 或 `null`） | **新增一块**。新块的 `id` 由服务分配，与模型切出来的块**共用同一套分配**（**不许两套编号空间**）。`keep` 省略时落 `true`（#26：**手工块默认「收」**——人已经看着图自己画了框，就不再拿像素统计否决他），并记 `decision.source = "human"`／`rule = "human_include"`；红笔读数（`ink`）**照带、只展示、不当闸门**。`bbox_norm` 非法 → 与 `move` **同一个拒绝形状**（`page_block_box_unusable` 那条既有码，§8）→ **这一块没新增** |
| `delete` （**本版（前端重构 / #17）新增**） | `block_id` | **删块**。两道硬约束见下；删掉的块往页文件的 `removed_blocks[]` 追加留痕，**绝不静默消失** |

**`add` 与 `delete` 的三条硬约束（本版（前端重构 / #17））**：

1. **已绑 `card_id` 的块永远不许删**——那张卡已经存在，删块会造出一条**孤儿绑定**。
   要「不要它」只能用既有的 `drop`（不收，块留在页文件里、带 `decision.rule = "human_drop"`，可审计）。
   违者 → **400** `bad_request`（`reason = "block_delete_bound_to_card"`），`details` 里带 `card_id`（§9）。
2. **从未入库的块可以删，但必须留痕**：往 `removed_blocks[]` 追加一条
   `{block_id, bbox_norm, question_no, removed_at}`（§10.2.1）。**绝不静默消失**——
   「删掉它才是静默丢题」这条既有裁决本版**收窄**（不再一律禁止删，改为：已绑卡的不许删、
   未入库的删了要留痕），不是取消（§11 偏离表）。
3. **每一次增删都要报数**：`delete` 成功时 `data.blocks_removed` = 这次请求删掉了几块；
   `add` 成功时 `data.blocks_added` = 这次请求新加了几块。**每一次 PATCH 回执都带这两个键**
   （0 也报），不许只在 `changed: true` 里含糊带过（与 §8「M＝0 也报」同一纪律）。
   `dry_run` 预演走**同一套**动作实现，所以预演也报这两个数。增删**对称**：
   两个都是会改变页文件的动作，只报一头等于让另一头偷偷发生。
   两个数**只数 `add` / `delete` 两条动作显式的增删**：`merge`（两块并一块）与
   `split`（一块拆几块）引起的块数变化不在里面——那两样的结果在 `edits[]` 与
   块列表里报，混进这两个数会让「这一块是谁删的」重新变模糊。

**为什么新增走 `PATCH` 而不是新开端点（本版（前端重构 / #17））**：`page_edit.py` 的分派表
（`DISPATCH` / `EDITABLE_ACTIONS`）的设计意图就是「**加动作只改这里**」，加一条 = 一行。
新端点等于把 `dry_run` 预演、拒绝形状、`EDITABLE_ACTIONS` 清单、回执形状**再实现一遍**，
而这个项目最怕两处实现慢慢漂移（#17 §3.5、§24）。两条动作进的是同一个闭集，
所以 `details.allowed` 里就有它们。

**`resegment` = 重置为预设（本版（前端重构 / #17）改动）**：

- 语义从「重跑一次切分」改成「**重置为预设**」：它是一次**破坏性**动作，**会丢掉人工的改动**。
- 请求体要**显式确认**：`{"confirm_discard_manual": true}`。没带这个键、而这一页又存在
  **任何**人工改动时 → **409** `resegment_needs_confirmation`，`details.discarded` 说明会丢掉什么：
  `{"manual_blocks": int, "removed_blocks": int, "mode_before": str}`——`manual_blocks` =
  `segmentation.mode == "manual"` 时页上的块数，`removed_blocks` = 留痕的条数，
  `mode_before` = **重置之前**那份块列表的来源（`model`／`manual`／`unavailable`）。
  **计数的只有前两个**，因为它们是**能数准**的；我们**不逐块记来源**（哪一块是人动的、
  哪一块是机器给的，没有这份账），所以**不报一个算不准的计数**——「界面说 3、服务说 2」
  比少报一个数坏得多。`mode_before` 是页上的一份**事实**（不是估计、不是计数），
  界面要拿它说清「这份块列表原来是**人**给的，重置会换成机器的预设」。
  **先把「将丢弃 N 处人工改动」报清**；这几个键的口径**只有一处实现**
  （`page_edit` 那个分派表旁边），界面照它显示、不许自己重算。
- 成功时：`segmentation.mode` 回到 `"model"`；`data.discarded` 用**同一个形状**报清**真的**丢了几块
  （不是预估值）；被丢掉的块进 `removed_blocks[]` 留痕——**追加，不清账**：
  重置把同 id 的块建回来时**也保留**旧留痕（§10.2.1 第 3 条：那一块确实被人删过一次，
  这件事没有因为重建而没发生）。
- **为什么这个能力要留**：模型切分最坏的失败是把**整页并成一块**，那时从零手画十道题
  比重摇一次预设差得远。但它既然是「重置为预设」，就必须**按破坏性动作对待**——
  **不许不声不响地覆盖人的劳动**（#17 §3.7、§31）。
- `checks` / `reconciliation` **照旧跑**（`segmentation.reconcile` 的三条判据，只报不改，见上表）：
  这次改动只动语义与回执，那两键一个字段都不动。
- ⚠ **两个名字相近、口径不同，别混**：响应里的 `removed[]` 是重切对账里「旧块在新切分里
  找不到位置重合的块」的对照（`classify_resegment`）；页文件里的 `removed_blocks[]` 是
  **人删过的块**的留痕。同一个响应里同时出现 `removed[]` 与 `discarded.removed_blocks`
  ——前者是机器对账的事实，后者是人的劳动被丢掉的读数。

**偏离与记账**（BRIEF 硬规则 3）：

1. `action` 与 `problem_type` 都是**闭集**：不在枚举里 → **400**，`details.allowed` 列出可取值。理由是拼错的值会**静默**让下游判据失效（拼错的题型会让「解答题没有标准答案属正常」失灵；编出来的题号会让「题号连续性」这条最强的检查失灵）。
2. **`bbox_px` 在边界被改之后置 `null`**：它是加了 1.5% pad 的盘上原值（D5），拖动之后必然过期；留着它下游会照**旧位置**算红笔统计。作废要显式（报 `page_block_box_changed`），不许当成「这一块没动过」。
3. **「建」与「入库」曾经是带归属的兜底 404**：路由在、动作没实现时，形状是 `{reserved: true, owner: "…归 #15"}`。**本版（前端重构 / #17）把这句话改准**：#15 之后这两个动作都已实现（请求/响应形状见上表），那条兜底 404 只剩历史口径；这条规矩本身仍然成立——**动作没实现时不许冒充成功**（ADR 0007 第 6 条），别让 #17 的读者以为这两个动作还没落地。
4. **`GET …/image` 是 #14 新增的一条读路由**：界面上「把块框画在整页照片上」要那张图，而照片与页文件同目录并列（D5）。它**不是渲染页面**（ADR 0007 第 2 条允许托管静态资源）。
5. **页载荷的 `id`／`image` 不许决定读写到哪**（#14 修的一处真漏洞）：`pages.save_page` 用 `page["id"]` 拼路径，于是一份文件名正常、载荷里 `id: "../problems/p-x"` 的页文件会让一次「改页」**覆盖一张真题卡**，且报出的 `page_path` 是假的、零警告（派生症状：`image` 可穿越读文件）。现在 `PATCH`／重切／取图三条路径写读之前都验 `page["id"] == 加载时用的页 id` 且 `image` 是**纯文件名**，不一致 → **400** `details.param == "page_id"`，一个字节都不动。

#### 10.2.2 模块与命令（B1 落地）

| 位置 | 是什么 |
|---|---|
| `server/pages.py` | 页文件的读写，以及两处**唯一实现**：`page_binding(catalog, card)`（这张卡有没有页绑定，`card_warnings` 与 #15 的审计都消费它）、`rebind(old, new)`（重切时按位置重合保留绑定） |
| `server/pages.py: allocate_card_id` / `assign_card_ids` | 题卡 id **首次入库时分配**：形状沿用 `p-<YYYYMMDD>-<6hex>`，日期是**入库日**（不是拍照日）；候选由**块的身份**（`<页 id>#<块 id>`）决定、唯一性由已在库的 id 集合保证 → 一页多块各得一个互不相同的 id（#9 验收 3） |
| `server/pages.py: rebind` | 重切对账的匹配：位置重合度 = **IoU ≥ `MATCH_IOU`（0.5）** 或 **重叠系数 ≥ `MATCH_CONTAIN`（0.8）**（口径的最终裁决在 #10，理由写在那两个常量旁边与 `rebind` 的 docstring 里）。一对一贪心；配上的新块继承旧块的 `card_id` 与 `keep`（人动过的两样）；配不上的 `card_id = null`（分配在入库那一刻）。**只给事实、不写盘不写题卡**；对照事实在并排的 `matches` 里（`matched_from`/`iou`/`contain`），不写进块 |
| `server/segmentation.py`（B2 / #10 新增） | **切分与对账**：`SEGMENT_SYSTEM`（抽取角色出候选块的提示词）、`parse_candidate_blocks`（模型输出 → 统一形状的块，**拒块逐条给理由**）、三条确定性判据（`check_question_numbers` / `check_overlaps` / `check_coverage`）、`reconcile`（汇总成一条结构化结论）、`classify_resegment`（把 `rebind` 的事实映射成新增／保留对照，**不写题卡不写盘**）。纯逻辑、不联网、不碰图（`ink` 由图像统计层给）。**不加 HTTP 端点**：v0 的只读立场不变 |
| `server/intake.py`（B4 / #12 新增） | **收入决策的唯一实现**：`decide_block`（痕迹语义 → 收／不收／待定，规则码见 §10.2.1）、`plan_decisions`（逐块写 `keep`+`ink`+`decision`，并报出「另有 M 道没有红笔痕迹、未入库」的枚举报告）、`include_blocks`／`set_keep_by_human`（一键补收／人改去留）、`run_intake`（读页 → 统计 → 问模型 → 写回的驱动）。统计消费 `ink.page_block_reports`（#11），页文件读写消费 `pages`（#9）——都不重写 |
| `server/intake_client.py`（B4 / #12 新增） | **抽取角色看红笔语义的接缝**：`SEMANTICS_SYSTEM`、`semantics_messages`（整页照片 + 块的归一化边界 + 红笔像素数）、`parse_semantics`（原文 → 语义，**从不抛异常**）、`image_data_url`（超 1600 长边才缩放重编码）。调用管道在 `model_client`；留档 tag `redpen-semantics`，**图片不入档** |
| `server/model_client.py`（B4 / #12 抽出） | **与角色无关的模型调用底座**：`default_transport`（标准库 HTTP）、`extract_json`、`ModelUnavailable`（把「没能问成」与「一次回答」分开）、`save_run`（`runs/` 留档）。判定角色（`server/judge_client.py`，#5）与抽取角色（`intake_client.py`）共用这一条管道；**提示词与消息形状按角色分家** |
| `python3 -m server.intake --page <页 id> [--apply] [--include\|--include-block <id>]`（B4 / #12 新增） | 一页的收入决策：**默认预演**（不写盘、**不问模型**），`--apply` 才问模型并写回；`--include` 是「一键补收」的命令形态。输出是 §2 的信封（与将来的 HTTP 端点同一形状）。**不加 HTTP 端点**：v0 的只读立场不变 |
| `server/coords.py`（B6 / #14 新增） | **两套坐标基准的显式换算**（唯一实现）：`crop_box_to_page`／`page_box_to_crop`／`crop_boxes_to_page`（掩膜框 → 整页）、`norm_to_px`／`px_to_norm`（按照片显示尺寸画块框）、`xywh_to_xyxy`／`xyxy_to_xywh`（**形状不同**，不是两种写法）、`contains_box`。读不出来的框一律 `None`（绝不返回零框）；形状判据**消费** `pages.usable_box`，不重写。**没有第二处坐标换算** |
| `server/page_edit.py`（B6 / #14 新增） | **页资源「改」动作的唯一实现**：`move_block`／`merge_blocks`／`split_block`／`drop_block`／`keep_block`／`set_problem_type`／`set_question_no`，`apply_edit` 按序作用并写回页文件。切换去留**调 `intake.set_keep_by_human`**（不另立一套判断）；`assert_page_payload_matches_id` 是写回前的不变式（载荷 id 不许决定写到哪） |
| `server/page_api.py`（B6 / #14 新增） | **页资源的 HTTP 翻译层**：`PageEndpoint.create`／`edit`／`resegment`／`image`／`commit`。它自己不实现任何规则——建在 `page_create`、改在 `page_edit`、三态在 `segmentation.classify_resegment`、入库在 `page_commit`、页 id 校验在 `pages.is_page_id`、拒绝路径在 `intake._load_page` |
| `server/page_create.py`（B7 / #15 新增） | **页资源「建」动作的唯一实现**：`create_pages`（照片 → 页 id → 存图 + 建页文件 + 跑切分 + #11 统计 + #12 建议去留）、`as_candidates`（切分接缝的归一化，建与重切共用）、`page_id_for`。`inbox.upload_files` 是「一次上传里的 `file` 段」的唯一实现（收件目录与建共用） |
| `server/page_commit.py`（B7 / #15 新增） | **页资源「入库」动作的唯一实现**：`commit_page`（只给收的块发卡号、id 首次分配、不重复生成、先写页再建卡、回写绑定、报告索引重建）。它**消费** `pages.assign_card_ids`（分配的唯一实现）与 `intake._load_page`（页加载与拒绝形状的唯一实现） |
| `server/audit.py`（B7 / #15 新增） | **落盘数据体检的唯一实现**：页↔卡双向对账（§8 的 B7 码表）、`CHECKS`（「我查了哪几项」的对外承诺）、`python3 -m server.audit [--data DIR]`。**只读盘、不修改、可随时重跑**；它消费 `pages.page_binding`、`warnings.card_warnings` 与 `warnings.duplicate_transcript_groups`，不另判一遍。回填是另一条显式命令（`server.backfill`） |
| `site/src/lib/split.js` + `site/src/components/SplitEditor.js`（B6 / #14 新增） | **切分修正界面**：重切三态显示、两套坐标分开画（`maskBoxOntoPage` 与 `server/coords.py` 一一对应）、七条动作拼 `edits`。界面**只提交「人做了什么」**，判定字段一个都不含 |
| `python3 -m server.backfill --data <dir>` | 存量卡 → 页文件的迁移，**默认预演（只读）**，`--apply` 才写；幂等（跑两次不改一个字节、不动题卡） |

**#9 自己不加任何 HTTP 端点**：本节那四个页动作归 #10/#12/#14/#15，HTTP 形状仍是预留。
已落地的写端点是 §10.1（#5）与 §10.3（#13）。

### 10.3 收件目录与手机上传页 —— #13（**已实现**）

录入的定义收敛成一句话——**往收件目录放一个文件**（ADR 0007 第 4 条）。
三条路（电脑拖拽／手机上传页／同步盘目录）之后走**同一条**管道：页 → 切分 → 审核队列。

- **收件目录**：`--inbox` / `AI_NOTE_INBOX`，默认是**数据目录**下面的 `inbox/`（§1）。
  它的绝对路径读在 `data.server.inbox`（§3）。
- **对外地址**：`data.server.public_base`（§3）——上传页链接与页锚点都用它拼，
  **不许**从 `--host` 推导；`data.server.upload_url` 就是拼好的上传页地址。

| 路径 | 方法 | 用途 |
|---|---|---|
| `GET /upload` | GET | **后端托管的单文件上传页**（无构建步骤、不依赖 Docusaurus 产物、**不引任何外部资源**）：手机上打开它拍照 → 上传 → 看结果。**这是「托管静态资源」不是「渲染页面」**（ADR 0007 第 2 条）：页面里没有一处服务端注入的值，请求一律走同源相对路径 |
| `POST /api/inbox` | POST | 往收件目录放文件（照片进来） |
| `POST /api/inbox/scan` | POST | 目录监视失效时的**手动等价入口**（`inotify` 在某些挂载与同步盘上不可靠，ADR 0007 的待验证项） |

`GET /upload`：`Content-Type: text/html; charset=utf-8`，`Cache-Control: no-store`。
它只把服务返回的 `warnings[].message` 与 `pipeline.segmentation.message` **原话**显示出来，
不自己发明文案（文案只有一份实现，在服务里）。

**`POST /api/inbox`** —— `Content-Type: multipart/form-data`，每个文件一个 part、名字必须是 `file`。
**一个 part = 一页**；一次传多个（＝电脑上的「一个文件夹」）= 多页，按上传顺序排。
body 上限默认 32MB，超了是 **413** `payload_too_large`（§9）。

```jsonc
{ "ok": true,
  "data": {
    "received": [{ "name": "IMG_0001.jpg",         // 客户端给的原名，只作显示（已取 basename）
                   "stored_as": "9f86d081884c.jpg", // 收件目录里的文件名 = sha256 前 12 位 + 后缀
                   "page_index": 0, "bytes": 123456, "sha256": "…",
                   "content_type": "image/jpeg", "already_present": false }],
    "grouping": { "kind": "single_page", "count": 1,
                  "note": "一个文件 = 一页；一次传多个（一个文件夹）= 多页，按上传顺序排" },
    "inbox": { "dir": "/abs/data/inbox", "files": 1 },
    "pipeline": {
      "segmentation": { "available": false, "reason": "not_implemented", "message": "…" },
      "commit":       { "available": false, "reason": "not_implemented", "message": "…" },
      "pages": [{ "page_index": 0, "stored_as": "9f86d081884c.jpg", "blocks": null }],
      "committed": false
    } },
  "warnings": [{ "code": "segmentation_not_implemented", "message": "…", "id": null }],
  "skipped": [] }
```

四条口径，写死在这里免得 #10/#9 与界面各猜一套：

1. **文件名取内容哈希**（`sha256[:12]` + 后缀），所以同一张照片重复上传不会堆两份
   （同步盘会把同一张再同步一遍）。第二次给 `already_present: true` + `already_in_inbox`（`hint`）。
   原始文件名只留在 `received[].name` 里给人看，**不参与落盘**。
2. **`blocks` 是 `null`，不是 `[]`**。「还没切」与「切出来 0 块」是两件不同的事；
   用空列表冒充会让界面看起来能跑——那是最坏的一种失败。
3. **`pipeline.segmentation` 是 #10 的接缝**：实现是「一个可注入的 `(照片路径) -> 块列表`」，
   接上之后块列表真的从 `pages[].blocks` 出来。**没接上时必须报
   `segmentation_not_implemented`**（§8），不许静默降级、不许编一个块列表。
4. **`committed` 与 `pipeline.commit`**：入库（按块生成题卡 + 回写绑定）归 #9/#15，
   #13 一个题卡都不建。这块必须显式，否则界面会以为照片已经变成题卡了。
5. **切分失败 → 502 `model_unavailable`**（D1，最终修复 pass 接上）：切分是模型调用，
   上游失败时 `POST /api/inbox*` 与 `POST /api/page*` 走同一个错误形状，
   `message` 里点名**哪一页**（收件目录里那个文件名）。**半截状态的边界**：照片已经
   在**收件目录**里（那是「收」这一步的产物；扫描那条路上的照片甚至不是这次请求建的），
   所以**不回滚照片**，`hint` 照实说明它没有丢；不留的是页文件、题卡与块列表——
   切分本来就不写盘。绝不落兜底 500。

**`POST /api/inbox/scan`** —— 无 body。目录监视（`inotify`）在多平台上不可靠，
所以它是那个**手动等价入口**，不是装饰：它认出现在收件目录里有哪些照片，逐条说清
「找到了但**没处理**、为什么」。

```jsonc
{ "data": {
    "inbox": { "dir": "…", "exists": true, "created": false, "files": 2 },
    "watch": { "implemented": false, "manual_entry": "/api/inbox/scan",
               "message": "目录监视没有实现：inotify 在某些挂载与同步盘上不可靠（ADR 0007 待验证项）。这个扫描就是它的手动等价入口" },
    "found": [{ "name": "synced-1.png", "bytes": 68, "sha256": "…",
                "content_type": "image/png",
                "status": "unprocessed",               // 切分不可用时
                "reason": "segmentation_not_implemented" }],
    "pipeline": { /* 同上传 */ } } }
```

`status` 只有两个取值：`unprocessed`（管道没就绪，没切）｜`segmented`（切了但没入库）。
`reason` 是挡住它的那个码（`segmentation_not_implemented`｜`commit_not_implemented`）。
**监视本身这一版不实现**（轮询线程在切分／页实体就绪前只是空转），所以把
`watch.implemented: false` 摆在响应里——不写出来，界面会以为有东西在盯着目录。

**命名空间先占住**：`/api/attempt/*`、`/api/page*`（`/api/inbox*` 已实现，不再预留）。
`GET`/`POST` 到预留路径返回 404，且 `message` 明说「这条路由是预留的、v0 还没实现」，
并带 `details.reserved`——含糊的 404 会让人以为是打错了字。

### 10.4 受控词表与大纲 —— 数据文件形状，**不是端点**（本版（前端重构 / #17）新增）

`CONTEXT.md`「科目」：科目是层级大纲的根（科目 → 章 → 节 → 点），也是错题分组的凭据；
它取自一份**受控词表**（`CONTEXT.md`「受控词表」：AI 不得自造标签，找不到合适项只能**提名**）。
这一节只写两份**数据文件**的形状——它们是「有哪些科目」「有哪些考点」的唯一来源，
经 `/api/index` 的 `subjects`／`outline` 给界面（§3）。**不新开端点**。

| 文件（**在数据目录下**） | 形状 | 是什么 |
|---|---|---|
| `<数据目录>/vocab/subjects.json` | `{"科目": ["数学", "物理", …]}`（与 `error-causes.json` **同形**） | 「有哪些科目」的唯一来源；`/api/index` 的 `subjects` 就是它的取值 |
| `<数据目录>/vocab/topic-outline.seed.json` | `{"大纲": {<科目>: {<章>: {<节>: [<点>, …]}}}` | 考点大纲的树；`/api/index` 的 `outline` 就是 `"大纲"` 里的那棵树 |

四条规矩：

1. **科目只有一个真源**：不从文件目录推、不从考点派生、也不许 AI 自造。
   卡上的 `subject` 不在表里 → `subject_unknown`（`warning`，§8）：读数是记录的原值，
   **不许静默改写成 `null`**。
2. **旧文件不兼容，这是破坏性改形**（本版）：`topic-outline.seed.json` 原来的形状是
   `{"name": …, "nodes": [{id, children}, …]}`，现在是 `{"大纲": {科目: {章: {节: [点]}}}}`。
   `name` 里那句「不是真实大纲」的自我否定**一并删掉**：那份自我否定是把「没大纲」写进了
   文件名与内容里，而新形状把它变成了真正的层级数据。
   **理由**：科目是导航的**根**（`CONTEXT.md`「科目」），大纲必须挂在科目下；
   一棵没有根的树在两级侧栏里没有地方可放（#17 §3.2、§18）。旧文件不改形就读不出来，
   所以这条改形要**明说**、要留痕（§11 变更记录），不许当兼容读取悄悄兜住。
3. **词表住在数据目录，不随仓库走**：`<数据目录>/vocab/`（与 §1 的 `--data` 一起走）。
   仓库里的 `data/vocab/` 是**测试语料与种子**——把词表当仓库文件读，就等于让
   「换数据目录」这件事在词表上失效。**读不出来不是 500**：读到的是一份空词表 + 一条
   词表级警告（§8），侧栏会空——那必须是一件**看得见**的事。
4. **大纲里只允许出现 `subjects` 里的科目**，否则报 `outline_subject_unknown`（`warning`，§8）：
   两份词表打架时，那个科目下的章／节／点谁都看不到（它不在侧栏的科目下）。
   以哪边为准是**人的决定**，服务只负责喊出来。

`subject` 是**题卡顶层字段**（§3.1）：缺字段／`null`／空串／纯空白一律算**未归类**，
`null` 是它的一等取值。没有科目的卡在侧栏的「未归类」下、**道数写在栏上**，
并且**不计入**任何科目的简报（§10.5）。

**存量回填是另一条显式命令（本版（前端重构 / #17）新增）**：

```
python3 -m server.subject_assign --data <dir> --map <表.json>          # 预演：只报告，不碰盘
python3 -m server.subject_assign --data <dir> --map <表.json> --apply  # 真的写回
```

映射表形状：`{"p-20261004-41c86b": "数学", …}`（键是题卡 id，值必须是**词表里的**科目）。
四条规矩：

1. **只认人给的映射表，不许按考点或题型去推**——猜错的科目比没有科目更坏，
   因为它**看起来是对的**，而没有人会去核对一个看起来对的答案（#17 §3.3）。
   表里没有的卡原样不动，只在汇总里报出「仍未归类」的道数。
2. **坏值一票否决**：表里出现一个不在受控词表里的科目 → `--apply` **一个字节都不写**、
   退出码 **1**（半途写一半比全不写更难收拾，与 `backfill` 的预演/`--apply` 分开是同一条纪律）。
3. **预演与 `--apply` 分开**：预演只报告「我打算做什么、为什么跳过哪几条」，不碰盘（先例
   `python3 -m server.backfill`）。
4. **它是写盘，所以不是审计**：审计（`server.audit`）只读；回填是一条单独的命令、要显式 `--apply`。

这份契约钉住的是上面四条**规矩**（命令名与映射表形状也在上面）；它的输出报告**形状**属于
实现细节（不规定字段名），但**内容有一条前提**：预演的输出**必须逐条列出被跳过的和为什么**
——哪条卡不在盘上、哪个科目不在词表里、哪份文件读不了，一条都不许吞掉。
「不许静默」管的是**有没有说**，不是**怎么排版**；一份只说「跳过 3 条」而不说是哪 3 条的
报告等于没说。**本节不涉及任何端点形状**，它只是让这份数据有了**唯一**的读者
（`server/subjects.py`，§12）；简报那两个新端点在 §10.5。

### 10.5 `/api/brief/<科目>` —— 简报（本版（前端重构 / #17）新增）

`CONTEXT.md`「简报」：就某个科目近期状态写出的一份文字总结，**由模型写**，因此要过验收才上岗；
它说的每一个数字都必须能在索引里逐字找回。这一节钉住两个端点与**数字闸门**。

| 动作 | 路由 | 请求 | 响应 `data` | 状态 |
|---|---|---|---|---|
| 读最近一份 | `GET /api/brief/<科目>` | 无 body；可选 `?at=YYYY-MM-DD` 取**历史上某一天**那一份 | `{brief: {…落盘形状 + is_latest/stale/new_problems…}, path}` | **本版新增（形状先钉住）** |
| 生成一份 | `POST /api/brief/<科目>` | 可选 body `{"window_days": 7}`（默认 7） | `{brief: {…落盘形状 + is_latest/stale/new_problems…}, path, run_id}` | **本版新增（形状先钉住）** |

**落盘**：`<数据目录>/briefs/<科目>-<YYYY-MM-DD>.json`（一天一份，保留历史，可回看上周的）。
落盘的是下面这个形状；**回执**在这个形状之上再加三个读数（`is_latest` / `stale` / `new_problems`，
见后），所以「文件里有什么」与「响应里有什么」差的就是那三个**现算**的键：

```jsonc
{
  "subject": "数学",
  "generated_at": "2026-10-04T09:39:57+00:00",  // 生成时刻
  "window_days": 7,
  "window_from": "2026-09-28",                  // window_until - (window_days - 1)，**含两端**
  "window_until": "2026-10-04",                 // covers_until 归一化到 UTC 之后的那一天
  "covers_until": "2026-10-04T09:39:57+00:00",  // **它读的那份索引快照**（= 那次索引的 built_at）
  "provider": "deepseek", "model": "deepseek-flash",   // 谁写的（两个字段，裁决 D2）
  "text": "……给人读的那段话……",
  "window_facts":   [{ "label": "冷却中的道数", "path": "stats.by_subject.数学.cooling", "value": 3 }],
  "history_facts":  [{ "label": "全部历史的道数", "path": "stats.by_subject.数学.problems", "value": 11 },
                     { "label": "未归类未计入", "path": "stats.unclassified", "value": 4 }]
}
```

| 键 | 含义 |
|---|---|
| `text` | **给人读的那段话**。「说得好不好」只由人读，当线索；它不是数据的来源，也不参与判定与打印 |
| `window_facts` / `history_facts` | 这段话**用到的每一个数字**。`path` 是**指向本次索引的 JSON 指针**（例如 `stats.by_subject.数学.cooling`），`value` 是用到的那个值。**两种窗口并列**：`window_facts` 数的是最近 `window_days` 天，`history_facts` 数的是全部历史——这正是「最近 7 天为主，同时并列全部历史」（#17 §7） |
| `covers_until` | 这些数字读的是**哪一份索引快照**（那次 `/api/index` 的 `built_at`）。索引是现算的，所以「数字依据哪一刻」必须写下来，否则「过期」判不出来 |

**窗口的起止写死**（本版（前端重构 / #17））：

- `window_until` = **`covers_until` 归一化到 UTC 之后的那一天**（§1：要比较的时刻一律先归一化到 UTC）。
- `window_from` = `window_until - (window_days - 1)`，**含两端**——7 天就是 `09-28 … 10-04`
  这 7 个日期（不是 8 个）。天数按日历日算，不按小时。
- **落盘文件名里的日期取 `window_until`**（不是 `generated_at` 的日期）：跨零点生成时两者可能不同天，
  而文件名要拿去与 `?at=` **逐字**匹配——文件名是接口的一部分，不许因为它恰好等于生成日就随便取一个。

回执里那三个现算的键（**本版（前端重构 / #17）新增**，GET 与 POST **都给**，
GET `?at=` 那份历史的也要给——侧栏看旧简报时要说清「这份已经不是最新」）：

| 键 | 含义 |
|---|---|
| `is_latest` | 这一份是不是该科目**当前最新**的那一份（`?at=` 取历史时通常为 `false`；POST 刚生成的那一份恒为 `true`） |
| `stale` | **这份简报生成之后又有题录进来了**。判据写死：该科目里 `created_at` **晚于这份简报的 `covers_until`**（它读的那份索引快照）的道数 > 0。索引是现算的，所以必须拿 `covers_until` 当那个时刻——不是文件 mtime、也不是 `generated_at` |
| `new_problems` | 就是那几道的**道数**（`stale: false` 时为 `0`） |

**侧栏那句话（「简报已过期：有 N 道新题没进去」）由服务给，界面不许自己算**（与 §6.1 第 5 条同规矩）。
§3 的 `briefs.<科目>` 是同一套读数的**索引级便利版**（只给最新那一份），两个地方口径相同。

**上岗闸门（硬规矩，写进契约）**：生成之后、**落盘之前**，服务必须把每一条 `path`
在**本次索引**里**解出来**，要求它与 `value` **相等**。**任何一条对不上，这份简报不落盘**，
直接 **502** `brief_unverifiable`，`details.facts` 列出对不上的那些
（`{label, path, claimed, actual, reason}`；`actual` 解不出来时为 `null`）。
理由：这个项目最怕的失败是一段读起来很顺、**数字却是编的**总结；而能**自动判**的只有
「数字对不对」——那一条必须是闸门。**同一道闸门也管未归类**：简报正文必须**显式**写
「另有 N 道未归类未计入」，并把 N 放进 `history_facts`（`path` 指 `stats.unclassified`），
于是那句话里的数字也过闸门。

`details.facts[].reason` 的取值（**本版（前端重构 / #17）新增**）：`path_unresolved`（路径解不出来）、
`value_mismatch`（解出来了但不等于 `claimed`）、`kind_mismatch`（类型不对）、
`missing_value`（那条路径在索引里就是 `null`）、`bad_fact`（这一条 fact 本身形状不对）、
`missing_fact`（这一条 fact 根本不在这份简报里，而文本引用了它）。
**为什么非加这个键不可**：上一条要求「解不出来」与「解出来不相等」分两档，
但 `actual = null` **同时覆盖**「解不出来」和「索引里正好是 `null`」——不加 `reason`，
这两档在回执里长得一模一样，而下一条 `missing_value` 正是要它们分开的那一档。

**`facts[].path` 的语法（本版（前端重构 / #17）新增）**——`path` 是**指向本次索引的 JSON 指针**，
语法写死在这里，**实现只有一处**（`brief.resolve_path`）：

```
path    := segment ("." segment)*
segment := key index*
index   := "[" 十进制非负整数 "]"
key     := 不含 "."、"["、"]" 的字符串，**逐字**匹配（中文键如 数学 不转义）
```

例：`stats.by_subject.数学.cooling`、`stats.unclassified`、`problems[3].mastery_cn`、
`problems[0].error_causes[0]`。求值顺序：每段**先按 `key` 取成员、再按 `[n]` 依次取下标**。

- **类型不对／越界都算「解不出来」**（不是 `null`、不是安全默认值）：拿 `[n]` 去下标一个对象、
  下标越界、键不存在、`path` 语法不合——一律 `path_unresolved`。
- 键里含 `.`／`[`／`]` 的字段**寻址不了**（语法里没有转义）：这是已知的表达力边界，
  撞上就换一条能解的路径，别发明转义。
- `value` 与解出来的值按 **JSON 等值**比对：数字按数字比、字符串逐字比、数组/对象**按深度相等**比。

**窗口与历史是叙事框，不是闸门的管辖范围（本版（前端重构 / #17）新增）**：
索引里**没有**「最近 N 天」这种聚合读数（`stats.by_subject.*` 全是**当前快照**）。
所以两个数组的**候选**各自有明确来源：

| 数组 | 候选（能写进 `path` 的东西） |
|---|---|
| `history_facts` | 科目级那六个读数（`stats.by_subject.<科目>.*`）与 `stats.unclassified`——「全部历史」正是当前快照拍的这一张 |
| `window_facts` | **窗口内录入**的每道题**自己的读数**：`problems[i]` 的 `attempts`／`cooling`／`cooldown_days`／`mastery_cn`／`last_verdict_cn`／`error_causes`／`topics` 等 |

**闸门只核数字真不真，不核它被放进哪个数组**：`window_facts` 里放了一条当前快照读数、
`history_facts` 里放了一条某道题的读数，都会照常通过——数组是**叙事框**（给读者看「这是最近的／这是全史的」），
闸门管的是**每一个数字都能在索引里逐字找回**。**不许**因为「它属于哪个数组」而放行一个解不出来的数字。

**一条诚实的边界（本版（前端重构 / #17）新增）**：`CONTEXT.md` 与 issue #17 举的例子里有
「错因分布」，但**这个语法没有过滤与聚合**，索引里**也没有按错因的聚合读数**——
所以「概念不清 4 道」这类话**核不出来**，闸门当场会拦下它。
正文只能引用**现成的读数**，或引 `problems[i].error_causes` 这种**可解路径**（数组按深度相等比对）。
这是设计代价、不是 bug：能自动判的只有「数字对不对」，而**编出来的聚合**正是这道闸门要拦的东西。
要让「错因分布」过闸门，得先在索引里长出一条真的聚合读数——那件事不在本版。

其余形状与既有约定对齐：

- **科目是「另一类」路径参数**（§1 的适用面）：这是中文（`数学` 走 `%E6%95%B0%E5%AD%A6`），
  服务端先按 URL 编码收下、解码，再拿受控词表**逐字**校验——不套用 §1 那条给 **id 类**参数的
  字符集规则。**本版明确**：科目不在词表里 → **400** `bad_request`
  （`reason = "subject_unknown"`，`details.param = "subject"`，`allowed` 给词表的取值），
  **不是 404**——这个科目不在受控词表里，是输入错（§9 那条 `subject_unknown` 与 §8 的
  逐卡警告**同名同事实**）。
  拿科目拼文件名之前必须先过这道白名单：**词表是白名单，所以穿越不可能**
  （与 `page_id_mismatch` 同一类纪律：内容不许决定写到哪、读到哪；写盘仍是
  「先写临时文件再原子替换」）。
- **没有简报 → 404**（`reason = "brief_missing"`，`details.available` 列出**实际有哪几天**）；
  `?at=` 要比的是那份简报文件名里的日期，**不许**按「最接近的一天」匹配。
- **模型失败 → 502 `model_unavailable`**（沿用既有约定，§9）：那一次**不落任何文件**
  （D1/D9「拒绝就该一个字节都不动」）。
- `window_days` 不是正整数 → **400** `bad_request`（`details.param = "window_days"`）；
  body 上限走**写端点那一档**（64 KiB，§9）。
- 该科目一道题都没有时**不拒绝**：数字全是 0，正文照写——闸门只管数字对不对。
- 生成要花一次模型调用，留档 tag `brief`（`runs/`，§10.1 的同一套留档，`run_id` 一并返回）；
  **`brief` 是它自己的模型角色**，`BRIEF_PROVIDER` / `BRIEF_MODEL`（§1）——**不复用抽取角色**，
  复用会让换模型时两个用途互相绑死（#17 §7）。

## 11. 契约决定与偏离记录

| 决定 | 为什么 |
|---|---|
| 图片端点是二进制字节，是「全部 JSON」的唯一例外 | §7.1。已在 issue #3 评论留痕 |
| 图片字段**改名**：proto / 现存的 `data/index.json` 用 `image`、`clean_image`、`mask_url`、`clean_stats.{boxes,…}`；契约用 `images.{original,clean,mask}` + `has_clean` | 旧名把「一张图」和「一个 URL」混在一个字段里，而且 `mask_url` 是拼出来的、不保证能取到。新名以 `images` 收口，取不到的给 `null`。**改名要记账**（BRIEF 硬规则 3），这张表就是账。旧文件保持原样，见下一条 |
| `warnings` 从 proto 的 `list[str]` 变成 `Warning[]`（`{code,message,id}`） | 界面要照原话显示，测试要按 `code` 断言。字符串列表两者都做不到。口径（该检查什么）全盘继承 proto，只是形状结构化了 |
| 新增 `skipped[]` | ADR 0007 第 6 条要的就是「我没做什么」。索引建不出来一条卡时，光有 `warnings` 说不清「少了一张」 |
| 索引**不落盘**，每次请求现算 | v0 只读，写路径归写端点。现算 = 永远没有陈旧索引。落盘与否**不改契约**（客户端看不出区别） |
| `data/index.json` 是 **proto 遗留物**，新服务不写它、不读它、不删它 | ADR 0001 说过索引是派生数据；proto 每次 `build_index()` 都写盘。新服务**每次请求现算**，不写任何文件。那个文件（及其旧形状）在磁盘上保持原样——它是私人数据、也是历史证据。**形状基准是这份契约，不是那个文件**；客户端一律经 HTTP 读索引 |
| `Problem` 在列表与详情里由同一个构造函数产出，详情是其超集 | §5 的硬承诺，防串题那类事故 |
| 加 CORS（`Access-Control-Allow-Origin: *` 与 `OPTIONS`） | `site/` 在 :3000，服务在 :8765，是跨源。没有它列表页一行数据都拿不到。服务只监听本机（ADR 0003），`*` 的暴露面就是本机 |
| 三个端点就三个端点，不加 `/api/queue`、`/api/health` | 队列（默认打印清单）由 §4 的读数与 §6.1 的 `screen_redo` 汇总额外提供，界面不重算规则；加第四个端点等于把同一套规则再实现一遍 |
| 拒绝用 **422**、模型失败用 **502**，绝不用裸 500 | 编排裁决 D1。请求合法而这道题不能判定 ≠ 参数写错；上游挂了 ≠ 服务端 bug |
| `source` 是 `auto`/`human` 两个机器取值，中文走 `source_cn`；`provider` 与 `model` 分开 | 编排裁决 D2。把中文写回 `source` 会让「有多少判定是机器给的」这类体检变成字符串匹配 |
| 进屏幕重做 = 有擦除图 **且** 可自动判定；缺擦除图不回退原图，但要把道数报出来 | 编排裁决 D4。原型那条 `or`（`proto/server.py:235`）会让「已审核但无擦除图」的卡渲染成 `<img src="None">`；而静默丢掉又是这个项目最怕的失败 |
| 判定的置信度门**只挡「对」，不挡「错」**：`equivalent:false` 一律判错（不看置信度），置信度 < 阈值只把「等价」降成看不清 | spec #1 映射表首行（「置信度 < 阈值 → 看不清」）是无条件的，与 #4 验收 2 字面冲突；**工单是权威**，且代价不对称——把一次真判错降级成「看不清」会让这张卡既不推进也不清零（学生看不出自己错了），而低置信度的「对」必须落向看不清（假「对」白拿一次掌握）。**这不是 bug，别照 spec 表格的字面去「修」**（`server/judge.py` 的映射与它的测试是判据） |
| 页文件的 `<hash12>` 取自 `page_image` 文件名；块边界以归一化坐标为规范基准 | 编排裁决 D5。照片同目录、与照片同寿命；归一化坐标不随重编码失效 |
| `deps`：`server/` 零第三方依赖（只用标准库 `http.server`） | 十轮实测的资产在 Python 里；少一个依赖就少一次打包与升级的谈判。**零依赖不等于这一层没有图像与模型的活**：抽取角色确实要吃图（`intake_client` 会缩放重编码），PNG 的编解码是 `server/ink.py` 自己做的，模型调用走标准库 HTTP——正因为这三件标准库都做得到，才不需要 PIL/numpy/requests。（本行原文写着「这一层没有图像与模型的活」，那句在 `intake_client` 落地之后就不成立了，第二轮评审作业单 **N1** 已裁决：**改这句话，不改代码**） |
| 对外地址**显式可配**（`--public-base` / `AI_NOTE_PUBLIC_BASE`），推导出来的打不开就报 `public_base_not_reachable` | ADR 0007 第 5 条那处已查明的债：原型从 `--host` 推，绑 `0.0.0.0` 就印 `http://0.0.0.0:8765`。默认推导仍然保留（本机自用最省事），但**必须能覆盖**，且推不出来时**要喊**（不许静默）。`Catalog.public_url` 是拼绝对地址的唯一入口——页锚点将来走同一个地方 |
| 收件目录的文件名取**内容哈希**（`sha256[:12]` + 后缀） | 同步盘会把同一张照片再同步一遍、手机上传页可能点两次。哈希命名让「同内容＝同一个文件」天然成立，也让将来页文件的 `<hash12>` 口径一致。原名只作显示 |
| `blocks: null` 表示「还没切」，与 `[]`（切出 0 块）区分开 | #13 交付时切分（#10）还没做。用空列表冒充会让界面看起来能跑——ADR 0007 第 6 条要的是「我没做什么」说得出来 |
| `POST /api/inbox` 是 v0 唯一的写端点，且只往收件目录写**新**文件 | ADR 0007 第 4 条把「放一个文件」定为录入的唯一入口；写新文件不是改已有数据，所以 §3 的 `read_only: true` 仍然成立，`read_only_note` 把那句话解释清楚 |
| 上传 body 上限 **413**，且判在**读 body 之前** | 服务将来要经局域网暴露给手机（#13）。读一个百 MB 的 body 再拒绝不是拒绝；这个响应之后关连接，因为 body 没读完，keep-alive 会串味 |
| `blocks: null`（还没切）与 `[]`（确实切出 0 块）的区分，**本版（前端重构 / #17）扩展到 `segmentation.mode`** | #13 那条区分的落点是「不许用空列表冒充『能跑』」。本版加了第三个维度：**「这份块列表是谁定的」**（`model` = 机器切出来的预设／`manual` = 人画的／`unavailable` = 还没有列表）。两件事必须分开：`null` vs `[]` 说的是**有没有列表**，`mode` 说的是**列表的来源与可信度**。`mode == "unavailable"` 时 `blocks` **必须是 `null`**——否则「我不知道」会被写成「这一页没有题」，那正是 #13 当时要挡的那句话（§10.2.1、#17 §3.6） |
| `removed_blocks[]` 与 `delete` 动作**收窄**既有的「删掉它才是静默丢题」那条裁决 | 旧口径：块只能 `drop`（不收，块留在页文件里、可审计），**不许删**，「不要它」只有 `drop` 一条路。本版开了一个**受控的删口子**（#17 §3.5、§30），因为「切分器多切了一个空框」这类东西留着只会污染对账读数。收窄写成两条硬约束，而不是取消那条裁决：**已绑 `card_id` 的块永远不许删**（删块＝孤儿绑定，要「不要它」仍只能用 `drop`）、**未入库的块删了必须进 `removed_blocks[]` 留痕**（哪一块、原来什么框、什么时候），而且留痕里**永远不许出现带 `card_id` 的块**——那是「删块那道闸破了」的直接证据。留痕**只增不减**、同一个 `block_id` 允许多条：「重置为预设」把同 id 的块建回来时也保留旧留痕，因为那一块确实被人删过一次——清账就是删留痕（§10.2.1、§10.2.1b） |

### 变更记录

| 版本 | 改了什么 | 为什么 |
|---|---|---|
| `5cdaf75` | 初版（= commit `765cd31`） | 工单 #3：契约优先，先把形状钉住 |
| 本版 | H1 补 `clean.boxes_norm`/`clean.manual` 的定义与裁剪图坐标标注、偏离表记下图片字段改名；H2 写明 `data/index.json` 是 proto 遗留物、形状基准是契约；H3 写明 `attempts_detail` = 卡里 `attempts` 原样（含 `provider`/`model`）；H4 定义 `mastery.credited`/`note`/`gap_days`、钉死 `channel` 枚举、补 `run_id` 与留档；M5 改正「服务不出 HTML」为「不渲染但托管静态资源」并给 `--public-base`/`--inbox` 补暴露面；M6 补 mask 的来源文件名；M7 `screen_redo` 改成两套 `bases`；M8 写死队列的快照语义；M9 加 `page_binding_missing`（`hint` 级）；M10/M11 拆开 `warnings` 与 `skipped` 两张码表、`level` 字段化、id 非法的说明改到 `hint`；M12/M13 修错引用并给 `stats` 标明分母；M14 加基线与本表。另：§6.1 增第 5 条——M 的那句话由服务给、界面照原话显示（真实渲染验证时发现界面自己又拼了一遍，出现重复行），N 必须逐个理由列数 | 契约缺口评审（4 高 + 7 中）：这些缺口会让 #5/#7/#9/#13 各自发明一套 |
| 本版 | 空 id（`GET /api/problem/`）与空 kind（`…/image/`）明确成 **400**（不是 404、且错要指向 `kind` 而不是 `pid`）；`Warning.level` 明确「总是显式发出来」；§6 术语滑词「同一个答案」改回「同一个**判定**」 | 独立验证查出的口径差：只差这「空」一档看着像漏网；`level` 有默认值却不出现在响应里，下游只能靠猜 |
| 本版 | #6 定点修正（§10.1 的第三种形态）：写明「只改既有那一次、不新建不重跑判定、不调模型（`run_id=null`）、不看能否自动判定」；`verdict`/`error_causes` 至少给一个否则 400；定位不到 → **404** `reason=attempt_not_found` + `details.available`，定位到不止一次 → **409** `ambiguous_attempt_at`；改判后 `source=human`、`confidence`/`provider`/`model` 置 null、`overrode` 记最初原判定（后两个键是超集）；掌握由 `recompute_mastery` **重放重算**，返回的 `mastery` 分两层（卡级终态 + 被改那一次的读数）；重复提交幂等。§9 码表补 409 与 `attempt_not_found`，并把 #5 两行从「预留、未实现」改成「已实现」；§0 的「无任何写端点」改成「除两种已实现形态外」 | 定点修正改的是历史里**某一次**，可能不是最后一次：就地打补丁会让后面几次的 `credited` 仍按旧判定算，所以只能重放重算（原型 `apply_attempt` 只追加，这条是新决定，先在 issue #6 评论里写明再定）。`overrode` 补 `provider`/`model`：契约原形状只有三键，「原本是哪台机器误判的」会永久丢失（spec #1 US 28）。409 与 `attempt_not_found` 都是「不许猜」：`attempt_at` 定位不到唯一一次时必须明确失败 |
| B5（#13） | §0 把「无任何写端点」改成「无任何**改已有数据**的写端点」并写明上传页已托管；§1 `--inbox` 默认跟着 `--data` 走、补 `public_base_not_reachable`；§3 `data.server` 加 `upload_url`／`reachable_from_other_devices`／`inbox`／`read_only_note`；§8 加收件目录四个警告码与 `inbox_part_empty` 跳过码；§9 加 **413** `payload_too_large`；§10.3 从「预留」改成**已实现**并钉死 `POST /api/inbox`／`POST /api/inbox/scan` 的形状（文件名取内容哈希、`blocks: null` ≠ `[]`、`watch.implemented: false`） | 工单 #13 落地。形状先在 issue #13 评论里记账再改这份文档（BRIEF 硬规则 3） |
| 本版（B1 / #9） | §10.2 从「预留」落成**具体形状**：加 §10.2.1 页文件字段（含 `bbox_norm` 是 xywh、`bbox_px` 是 xyxy 的对照表）与 §10.2.2 模块/命令表（`server/pages.py`、`allocate_card_id`/`assign_card_ids`、`rebind`、`python3 -m server.backfill`）；§8 加 `page_binding_lost`（`level: "warning"`），并把 `page_binding_missing` 标成 B1 落地；§12 模块角色加 `server/pages.py`；§12.1 补页的纯逻辑测试接缝 | 工单 #9 验收 1/2/3。**先写进 issue #9 的评论再动手**（BRIEF 硬规则 3）。要点：两套坐标基准（整页 vs 裁剪图）不许混用；「一页多块 id 互不相同」与「重切保留绑定」是同一份逻辑；旧卡缺绑定是**提示**、页↔卡对不上账才是**警告** |
| 本版（B3 / #11） | §12 模块角色加 `server/ink.py`；§10.2.1 预留的 `ink` 键点明由 `ink.page_block_reports` 产出 | 工单 #11 验收 2「体检与筛选读的是同一个常量」。规格原文说「那**一个**阈值」，但源码里是**两颗**回答不同问题的常量（像素级 `sat >= 60` 见 `proto/slice.py:849`/`:921`，框级 `in_color > 20` 见 `:888`）——收成一颗是错的。真正的重复是 `60` 在体检与擦除里各写了一遍。**先写进 issue #11 的评论再动手**（BRIEF 硬规则 3）。本版**不改任何响应形状**：`ink` 是数据文件里的键，不是端点字段 |
| 本版 | §10.1 的 `mastery.cooling` 注释改写成「**这次重做发生时**是否处于冷却窗口」——判定门在写入 `last_attempt_at` **之前**取，并写明 `cooling:true` → `credited:false`（只热身） | 原措辞「更新之后是否仍在冷却」按字面读**恒为真**，与同一段示例（`credited:true` + `cooling:false`）自相矛盾。#5 按 proto 口径实现（`proto/slice.py:620` 先算、`:631` 后写），#6 定点修正要读这段语义——留着矛盾注释会让它按字面把冷却算错（`docs/acceptance-log.md:277` 记的「真错，不是风格问题」）。§3.1 表里 `Problem.cooling` 那行**不改**：它是索引的实时读数（当前时刻 vs 上次重做＋7 天），与写入顺序无关 |
| B5 修正（#13） | §12.1 清掉合并时留下的一条**重复 bullet**（「纯逻辑（冷却／排序／可判性／警告码…）」出现两次），并把同一条改成「拒绝路径也要有测试」——记下 `POST /api/inbox` 的「一次传的全部是空文件」那条拒绝分支写错变量名会退化成兜底 500 的教训 | 两个下游工单（#6／#11）在集成分支 tip 上跑 `ruff check server/` 报 `F821`，卡着它们的验收。契约层面要留下的不是那一行修正，而是**判据**：每条会拒绝的分支都要有一条断言形状的测试 |
| 本版（B4 / #12） | §10 状态表补「块已长出 `question_no`/`ink`/`decision`」；§10.2.1 块形状加 `ink`（#11 产出、#12 写入）与 `decision`（新增），并给 `rule` 的七个取值一张表；§8 加「收入决策码表」8 条（含 `hint` 级）；§10.2.2 与 §12 加 `server/intake.py`／`intake_client.py`／`model_client.py` 与 `python3 -m server.intake` | 工单 #12 验收 1/2/3。**先写进 issue #12 的评论再动手**（BRIEF 硬规则 3）。要点：`keep` 只说去留、`decision` 说为什么（决策只存内存＝漏题永远查不出来）；「判不准 → 收」是决策不是兜底（代价不对称），而「统计读不出来」是**待定**（`null`）不是「不收」；人补收/丢弃过的不许被重跑抹掉。**不加 HTTP 端点**：形状归 #14/#15，本版只给服务层入口与 CLI |
| 本版（B2 / #10） | §8 加「页级对账码表」16 条（含 `hint`/`warning` 两级，并写明 `replaced` 恒为 0 是结构性的）；§10.2.2 更新 `rebind` 的口径（IoU ≥ 0.5 **或** 重叠系数 ≥ 0.8）并加 `server/segmentation.py`；§12 加该模块的角色 | 工单 #10 验收 1–4。**先写进 issue #10 的评论再动手**（BRIEF 硬规则 3）。要点：三条判据都要**返回结构化结论**而不是打印（ADR 0007 第 6 条）；#9 留给 #10 的是 `MATCH_IOU` 的**口径裁决**——纯 IoU 会把「同一个块被重切细化了边界」判成旧块消失，从而孤立一张已审核的卡，所以补一条包含判据；重切三态里「替换」不允许作为自动结果出现。`inbox.py` 的 `segmenter` 接缝**本版没接**：接它需要「模型失败 → 502」的错误契约，而 `run_pipeline` 现在会让异常落到兜底 500 |
| 本版（B2 / #10 收尾） | §8 的页级对账码表**改回 §2 的 `Warning` 形状**（`{code, message, id, level}`，页级 `id` 为 `null`、卡级填卡 id；`rebind` 交回的事实由出口补级别，表里没有的码按 `warning` 报） | 上一条预告写的 `{code, level, message}` 与 §2「形状一致」自相矛盾，实测也确实漏了：`pages.rebind` 手搓的三条码（`block_without_box`/`block_not_an_object`/`block_removed_with_card`）**没有 `level`**，而 `classify_resegment` 原样透传——于是页级对账的出口会漏出「没有 level 的警告」，下游只能靠猜（正是 §2 要防的）。同时发现 `classify_resegment` 在调 `rebind` 前用 `isinstance(b, dict)` 静默 filter 掉块列表里的非对象项（`rebind` 明明会报 `block_not_an_object`）——与 `proto/server.py:320-321`、`:933-936` 同一种写法。两条都有会红的测试钉住；记账见 issue #10 的评论 |
| 本版（B6 / #14） | §10 状态表把 §10.2 改成**部分已实现**并加 §10.2.1b（四个动作的 HTTP 形状、七条 `action` 的闭集、五条偏离记账）；§10.2.2 与 §12 加 `server/coords.py`／`page_edit.py`／`page_api.py`／`site/src/lib/split.js` | 工单 #14 验收 1/2/3。**先写进 issue #14 的评论再动手**（BRIEF 硬规则 3）。要点：①「改」与「重切」落地，「建」「入库」归 #15 且**不冒充成功**；②`action`／`problem_type` 是闭集，拼错的值会**静默**让下游判据失效；③边界改动后 `bbox_px` 作废置 `null`（它是加了 pad 的盘上原值，留着就是「安静的谎」）；④**页载荷的 `id`／`image` 不许决定读写到哪**——修掉一处**真漏洞**：一份文件名正常、载荷 `id: "../problems/p-x"` 的页文件会让一次「改页」覆盖一张真题卡，且 `page_path` 是假的、零警告；现在写读前验 `page["id"] == 加载时用的页 id` 且 `image` 是纯文件名，不一致 → 400、一个字节不动；⑤隐私回归抓出一处真泄漏（真实页哈希进了 `build/`）与两处夹具里的真题原句，已清理 |
| 本版（D10 / #6） | §10.1 的「**掌握只能重算**」改成「**按 payload 是否改判据分流**」：改判（`verdict` 与现值不同）仍必须重放重算；只改错因（没给 `verdict` 或与现值相同）**不重算**，卡上 `mastery` 与既有 `attempts[i].note` 逐字保留。`mastery` 的「被改那一次的读数」由 `mastery.peek_step` **只读地**给（与重放共用同一份冷却门，故两条路径逐字一致，幂等不受影响）。§12 的 `mastery.py`／`amend.py` 两行跟上 | 独立验证者在 #6 上找到的边界：**只改 `error_causes`** 时，若卡上存的 `mastery` 不是历史的重放固定点，`recompute_mastery` 会把它重放改写——一张「已毕业、连对 5 次」的卡因一次纯标注编辑被**静默重置回池**，连 `attempts[i].note` 也被重写。理由三条：无关编辑不该销毁人动过的状态（spec #2 同一原则）、不许静默（ADR 0007 第 6 条）、漂移该由审计报出来（#15）而不是被顺手改掉。**修法是分流，不是取消重放**（#6 验收 1 一条都不许取消） |
| 本版（B7 / #15） | §10 状态表把 §10.2 改成**已实现**（四个动作都有路由）；§10.2.1b 补「建」「入库」的请求/响应形状与各四条规矩；§10.2.2 与 §12 加 `server/page_create.py`／`page_commit.py`／`audit.py`；§8 加「页资源建/入库与审计码表」14 条（含 `hint` 级），逐卡表加 `problem_transcript_missing`；§9 的 `problem_missing_field` **语义收窄**成「连 `problem` 对象都没有」并写明「题面还没转录不再跳过」；§12.1 补审计与端到端的测试接缝 | 工单 #15 验收 1/2/3。**先写进 issue #15 的评论再动手**（BRIEF 硬规则 3）。要点：①入库建的是**骨架卡**（块上只有边界与题号），所以索引必须**收录还没转录的卡**并报 `problem_transcript_missing`——落盘了看不见就是静默丢题，与 spec #2 第 17 条冲突；②只给「收」的块发卡号（丢给弃块发号＝幽灵绑定）；**先写页再建卡**是幂等的一部分（反过来会换 id）；③页绑定那一条**不另造码**，消费 `pages.page_binding`：旧卡 `hint`／页↔卡对不上账 `warning`，两级不许混；④同页题干逐字相同的判据与索引级**共用一份实现**（`duplicate_transcript_groups`），没有模糊比、没有相似度；⑤审计 `checked[]` **永远在**、退出码只认 `warning` 级发现；⑥真数据只读复核（跑前跑后 `data/` 逐字节不变）：两张存量卡的 `page_binding_missing` 都是 `hint`，那张解答题卡（标准答案空、考点空）的两条 warning 是真问题，没被提示淹掉 |
| 本版（#12 数据安全修复） | §8 加「写盘路径的纵深防御」三个码（`page_id_mismatch`/`page_image_unsafe` 为 400 + `details.param`、`filesystem_error` 为 500）；§9 补 `internal_error`/`bad_request` 的 `reason` 取值；`save_page(catalog, page, *, page_id, …)` 的写盘路径只认 `page_id`，身份闸在算路径之前、预演走同一道闸 | 独立验证者实测的真反例：页 id 的校验只挡「传进来的 id」，挡不住「页文件里 `id` 字段」——`save_page` 拿它拼路径，`{"id":"../problems/p-20261004-41c86b"}` 会让 `run_intake(apply=True)` 把整份页 JSON **覆盖一张真题卡**（受害者 md5 变、`warnings == []`、CLI rc=0），`"id":"../../problems/p-nope"` 则是 CLI 裸 `FileNotFoundError`。同类入口还有 `image` 拼 `image_path`（可借读取穿越 `pages/`）与 `backfill` 的写回。**先写进 issue #12 的评论再动手**（BRIEF 硬规则 3） |
| 本版（最终修复 pass） | §9 补 `bad_request` 的 `reason: "body_too_large"` 与「写端点 body 的显式上限 64 KiB」一段；§10.1 的错误表加一行；上限按路由由 `Api.body_limit` 一处判，`app.py` 在读 body 之前就用它 | 最终修复 pass 作业单 2（来源 #5 的 merger 给的真反例：2MB 合法 JSON → 200，`_read_body` 按 `Content-Length` 整段读进内存、作答还会发给模型）。#13 之后服务要经 Tailscale 给手机用，所以这条要在**进模型之前**拒绝且不留记录（D1/D9）。裁决用 **400**（不是上传那档的 413）：写端点的 body 只有几个键，超限是「参数写错」那一类；上传那档仍是 413（见 §10.3） |
| 本版（最终修复 pass） | §10.3 加第 5 条：切分接缝上的模型失败 → 502 `model_unavailable`（`message` 点名哪一页、`hint` 说明照片没丢、不回滚收件目录里的照片）。`errors.model_unavailable` 加可覆盖的 `hint` | 最终修复 pass 作业单 7（#10 明说的未做项）。`inbox.run_pipeline` 以前直接调 `segmenter(...)`：`ModelUnavailable` 只能靠 HTTP 层的兜底 catch 变成 502（`details.id` 是 `null`），既不点名哪一页，`hint` 还写着「没有留下任何记录」——可照片明明已经收在收件目录里，那是**说反话**。现在接缝显式走 `errors.py` 的唯一实现；扫描那条路上的照片不是这次请求建的，回滚它在语义上根本不成立 |
| 本版（最终修复 pass） | §8 没有改码表，但补齐了三个出口的 `level`：`catalog.problem_id_mismatch`、`pages.backfill_pages` 的 5 条、`pages.rebind` 的 3 条一律走 `warnings._warn`（裁决 (a)：`rebind` 不是「内部结构、由消费方补级别」）；`card_warnings` 的 `standard_answer_missing` 在 `problem.type == "solution"` 时降为 `hint`（非解答题仍是 `warning`） | 最终修复 pass 作业单 3/3b/5/9。§2 要求 `level` **总是显式发出来**，手搓 dict 让它取决于「谁在读」；解答题没有标准答案是**预期状态**（原型 `--audit` 一直这么降级），报 `warning` 会把它淹在噪声里，与 D3/D4「提示与警告分级」的一贯口径冲突 |
| 本版（R1：对账进生产路径） | §10.2.1b：`POST /api/page` 与 `POST …/resegment` 的响应加 `checks` / `reconciliation` 两键（定义见该节的表）；§12 的 `server/ink.py`／`server/segmentation.py` 两行点明 `page_ink_regions`／`reconcile_response` | 第二轮修复 pass R1。#10 交付了确定性的三条判据，但**只有测试调用它**——生产路径上「静默丢题」照样是静默的。现在两条路都跑并结构化报出来，**只报不改**（切分仍由人确认）。覆盖率必须真的跑：`ink.page_ink_regions` 是整页墨迹区域唯一的实现（八连通，红笔∪深色），读不出来时明说「没查」而不是冒充通过 |
| 本版（R2/R9：码表去重） | 回填那条路**不再自造第二个码**：`bbox_norm` 缺/退化（块退化为整页）与 §8 已定的 `block_without_box`（「与 `pages.rebind` 同一个码、同一件事」）统一，`server/pages.py` 收成一个 `BLOCK_WITHOUT_BOX` 常量（回填与 `rebind` 三处共用）。§8 码表**不变**（被删的那个码从来没进过表） | 第二轮修复 pass R2/R9。同一个事实两个码会让界面出现两种说法、让「按码统计」永远对不上；R9 一并收掉 `page_commit.py` 里那句「已记进报告、留给最终修复 pass 收」的过期承诺——那笔账现在真的记在这里 |
| 本版（R3–R6、N1、N4） | §9 的 `internal_error` 原因清单改成显式取值列表并登记 `internal_error`（R5）；§11 的 `deps` 行改掉「这一层没有图像与模型的活」（N1）；§11 偏离表加判定的置信度门只挡「对」那一条（N4）。**响应形状没有别的变化** | R3（六条会拒绝/跳过的分支补上断言形状的测试：`page_commit_block_without_box`／`page_commit_blocks_skipped`／`intake_block_unknown`／`problem_file_unreadable`／`problem_not_dict`／`page_ink_draft_excluded`，六条全可达、无需 skip）、R4（读不了的卡不再安静地 `continue`：重切与入库都报 `card_file_unreadable` + 文件指针 + 明说它没进对账）、R6（用户可见文案的术语滑词「答案」→「作答」：`errors.not_auto_judgeable` 的 `hint` 与界面侧两处，`grep` 那句旧措辞在 `server/` 里为空（本表这一行的引用是唯一的仓库级命中））、N1（零依赖不等于没有图像与模型的活）、N4（spec #1 表格与 #4 验收 2 的字面冲突按工单实现，记账免得被当 bug 查） |
| 本版（ADR 0008） | §1 数据目录默认值从「仓库根的 `data/`」改成**用户数据目录**（Windows `%APPDATA%\ai-note`、macOS `~/Library/Application Support/ai-note`、其它 `$XDG_DATA_HOME/ai-note`）；§10.1 的留档改成「**数据目录**下的 `runs/`」；§1 收件目录的例子改成 `--data <dir>` → `<dir>/inbox/`。**响应形状一个字段都没变** | 运行时产物不该住在项目目录里：`git clean`／重新 clone／切分支都可能碰掉资料，`git status` 常年靠六条忽略规则兜底，而且生产数据与测试语料抢同一个目录——「跑测试别写到真数据」本该由结构保证，不该靠纪律。仓库里的 `data/` 降级为**本机测试语料**；默认值改成在**建 parser 的那一刻**算（`.env.local` 是那时才灌进环境的，import 时算就等于让写在里面的 `AI_NOTE_*` 静默失效） |
| 本版（真 socket 上的 `PATCH`） | §10.2.1b 的 `PATCH /api/page/<id>` **在真实 socket 上才算真的可用**：`server/app.py` 之前只挂了 `do_GET`／`do_OPTIONS`／`do_POST`，这条路由只在 `Api.handle` 这个纯函数接缝上存在；`OPTIONS` 的 `Allow` 与 `Access-Control-Allow-Methods` 补上 `PATCH`（这两处以前只有 `GET, POST, OPTIONS`）。**响应形状一个字段都没变** | 契约 §10.2.1b 早就写了这条路由，缺的是**接线**——这是「实现没跟上契约」，不是契约改了。它活了很久，因为唯一吃它的测试直接调 `api.handle("PATCH", …)`，**绕过了 socket**：判据断言的层级与坏掉的那一层错开一格。真实后果有两层：真浏览器拿到的是框架自带的 **501 + `text/html`**（不是 §2 的信封），而跨源预检因为方法清单里没有 `PATCH`，**更早一步就把请求拦下了**——切分修正页唯一的写路径等于不存在。现在有一条走真 socket 的测试盯着它（摘掉 `do_PATCH` 它会红） |
| 本版（前端重构 / #17） | **A 科目成一等字段**：§3.1 题卡记录加 `subject`（`null` = **未归类**）；§8 逐卡加 `subject_missing`（`hint`，与 `page_binding_missing` 同级）与 `subject_unknown`（`warning`，值不在词表**不许静默改写成 `null`**），并按实现订正「逐卡码 `level` 全是 `warning`」那句（#17 §3.9）；§3 的 `stats` 加 `by_subject` 与 `unclassified`，并把不变式 `sum(by_subject[*].problems) + unclassified == stats.problems` 写进正文。**B 词表随索引一起给**：`/api/index` 加 `subjects`／`outline`／`briefs`（`stale` = 「这份简报生成之后又有题录进来了」、`new_problems` 是那几道的道数）；`outline` 里只许出现 `subjects` 里的科目，否则 `outline_subject_unknown`（`warning`）；写明「一次请求画出整棵树、不许露『有科目但没有大纲』的中间态」。**C 数据文件**：新 §10.4 钉住 `<数据目录>/vocab/subjects.json`（`{"科目": […]}`，与 `error-causes.json` 同形）与 `topic-outline.seed.json` 的**破坏性改形**（`{"大纲": {科目: {章: {节: [点]}}}}`，旧 `name` 里那句「不是真实大纲」的自我否定一并删掉）以及理由（**科目是导航的根，大纲必须挂在科目下**），并写明存量回填命令 `python3 -m server.subject_assign --map <表.json> [--apply]`、映射表形状（`{"题卡 id": "科目"}`）、**坏值一票否决**（表里有一条不在词表里 → `--apply` 一个字节都不写、退出码 1）与**不许按考点推科目**（猜错的科目比没有科目更坏），并写明预演**必须逐条列出被跳过的和为什么**（形状是细节，说的是内容）；§8 补 6 条词表级警告（读不出来**不是 500**）；§1 把路径字符集规则的**适用面**写准（只管 **id 类**参数；科目是另一类：URL 编码 + 词表逐字校验）。**D 页文件**：§10.2.1 加**页级 `subject`**（与题卡同名同义，`null` = 未归类）、`segmentation`（`model`／`manual`／`unavailable`）与 `removed_blocks[]`（**只增不减、同 id 允许多条**）；`mode == "unavailable"` 时 `blocks` **必须**是 `null`；留痕里**永远不许**出现带 `card_id` 的块；写明**切分不可用时也建页**（照片落盘、页文件照建——推翻「切分不可用就不建页」那条旧裁决，ADR 0005 / #15）。§10.2.1b「建」的请求形状加**可选** multipart 文本字段 `subject` 与三条规矩（没给 → 未归类；词表在而取值不在 → **400** `subject_unknown`；**词表本身不在 → 收下并带回词表级警告**——录入摩擦是这类工具的头号死因，ADR 0006 决定第 5 条），响应 `pages[]` **回显 `subject`**（没给就是 `null`，不是缺字段——回执不回声，界面就没法确认科目真被收下了）。**E PATCH 的动作闭集加两条**：`add`（新块 id 与模型块**共用一套分配**；`bbox_norm` 非法 → 与 `move` 同一个拒绝形状）与 `delete`（**已绑 `card_id` 的块永远不许删** → 400 `block_delete_bound_to_card` 带 `card_id`；未入库的块删了必须留痕）；回执加 `blocks_removed` 与 `blocks_added`（**每一次增删都报数**，0 也报，增删对称），并写明**新增走 PATCH 而不新开端点**的理由（加动作 = 一行；新端点要把 `dry_run`／拒绝形状／`EDITABLE_ACTIONS`／回执再实现一遍，最怕两处漂移）。**F `resegment` 语义改成「重置为预设」**：要 `{"confirm_discard_manual": true}`，否则 **409** `resegment_needs_confirmation` + `details.discarded`（`{manual_blocks, removed_blocks, mode_before}`——**计数只有前两个**，因为只有它们能数准；不逐块记来源，所以不报一个算不准的计数；`mode_before` 是「重置前那份块列表是谁给的」这份**事实**，不是计数）；成功时 `segmentation.mode` 回 `"model"`、回执报**真的**丢了几块、删掉的块进 `removed_blocks[]`；`checks`／`reconciliation` 照旧跑。**G 简报端点**：新 §10.5 的两个端点、落盘 `<数据目录>/briefs/<科目>-<YYYY-MM-DD>.json` 的形状（`window_facts`／`history_facts` 的 `path` 是指向本次索引的 JSON 指针）与**上岗闸门**（任何一条 `path` 解不出来或对不上 → **不落盘** + 502 `brief_unverifiable` + `details.facts`，其中 `reason` 六档把「解不出来」与「索引里正好是 null」分开；它与「模型问不成」的 `model_unavailable` 是两个码，**处置不同：一个重试无用、一个可以重试**）；§10.5 还写死 `facts[].path` 的语法（`brief.resolve_path` 是唯一实现；类型不对/越界都算解不出来；含 `.`/`[`/`]` 的键寻址不了）、窗口起止（`window_until` = `covers_until` 归一化到 UTC 的那一天，`window_from` = `window_until - (window_days - 1)` **含两端**，**文件名取 `window_until`**），以及两条边界（**窗口/历史只是叙事框**，闸门只核数字真不真、不核它在哪个数组；**没有过滤与聚合**，所以「错因分布」这类话核不出来，正文只能引现成读数或可解路径）；回执另加三个现算的键 `is_latest`／`stale`／`new_problems`（**历史那一份也要给**，`stale` 的判据写死成 `created_at > covers_until`）；未归类**不计入**任何科目简报，但正文必须写「另有 N 道未归类未计入」且 N 进 `history_facts` 受同一道闸门管。**H 切分与简报的接线**：§1 补 `SEGMENTER_PROVIDER`／`SEGMENTER_MODEL` 与 `BRIEF_PROVIDER`／`BRIEF_MODEL`（默认沿用抽取角色的默认值）与四个角色的上岗闸门；§12 加 `server/subjects.py`、`server/brief.py`、`server/brief_client.py`／`server/segmenter_client.py` 与 `server/subject_assign.py`；§12.1 加模型上岗验收 `python3 -m pytest server/tests/test_acceptance_models.py -v`（**换模型就要重跑，不过考不许上岗**；需要密钥，**没密钥 skip 而不是 pass**）与 `segmenter` 的三条闸门（视觉探针 + 固定照片集上的人工判定 + `segmentation.py` 的三条对账判据，**三条都要过**，固定照片集落进仓库当夹具）。**I** §0 范围、§9 错误码、§10 状态表、§11 偏离表同步；§10.2.1b 那句「「建」与「入库」是带归属的兜底 404」按实现改准（#15 之后它们早已落地，留着会让 #17 的读者以为还没实现）；§8 的 `subject_unknown` 警告与 §9 的 `subject_unknown` 400 **同名同事实**（R2/R9：同一个事实不许两个码；码表分开、两处互相指认）。**本版只改契约，实现另跟** | 工单 #17（前端重构）的 §3 与 §10 落成契约（契约先行）。要点：①**未归类是一等状态**，不是缺字段——它要有自己的兜底栏与道数，要一条 `hint` 而不是被当成脏数据；②**科目只有一个真源**（受控词表），卡上的值不在表里**不许被静默改写成 `null`**（读数是记录的原值，谁改的谁喊）；③侧栏要**一次请求画出整棵树**，所以词表随索引走、不新开端点；④**「我不知道」与「这一页没有题」必须分得开**（`blocks: null` vs `[]`，再加一层 `segmentation.mode`），而切分不可用时**页文件还是要建**——不建，人就无从手画；⑤**删块要留痕、已绑卡的不许删**——删掉它才是静默丢题；⑥**重置为预设是破坏性动作**，必须先报清将丢弃几处人工改动，不许不声不响覆盖人的劳动；⑦**简报最怕的是一段读起来很顺、数字却是编的总结**——能自动判的「数字对不对」必须是闸门；⑧**换模型就要重跑验收，不过考不许上岗**，固定照片集落进仓库让这句话成为一条可执行的命令；⑨**词表缺席不许挡住录入**——录入摩擦是这类工具的头号死因（ADR 0006 决定第 5 条），所以「词表在而取值不在」是 400、「词表本身不在」是先收下再喊；⑩**同一个事实不许两个码**（R2/R9）：卡级自检与输入拒绝共用 `subject_unknown` 这个名字，层次不同、码表分开 |
| 本版（补 `GET /api/page/<id>`；接上 `DELETE`／`PUT`） | §10.2.1b 的动作表加一条**读**：`GET /api/page/<id>` → `{page_id, page_path, image, page}`（`page` 是页文件原样）。§10 状态表从「四个动作」改准成「**六个**」。`server/app.py` 补 `do_DELETE`／`do_PUT`：这两个方法自己不做路由，只是把请求送进 `Api._route`，好让「用错方法」真能得到 **405 带 `allowed`**（§5.1）**并且是 JSON 信封**（§2）。**响应形状只多不减** | 界面要打开一页来改，第一步就是**读**；而 `page_api` 一直只有 `PATCH`——于是「读一页」只能拿一次**空修正的预演**（`PATCH {dry_run: true, edits: []}`）去凑，让**读**依赖一条**写形状**的路由。这与 `do_PATCH` 那次是**同一个病根**：**路由只活在纯函数接缝里**（`SplitEditor` 的 `fetchPage` 从 #14 起就踩在这条死缝上，真实 socket 上是 405——它的测试全靠注入 `pageData`，所以从没红过）。**判据的层级与坏掉的那一层错开一格，是这一类缺口能活下来的唯一原因**；现在两条路由各有一条**真 socket** 的测试盯着，用错方法的 `allowed` 也在断言里 |


## 12. 模块角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `server/autojudge.py` | **「能不能自动判定」的唯一实现**：三个拒绝理由与它们的优先级。`#5` 的写端点**必须调用它**，不许重写；界面只显示 `reason_text`，不许自己再判一遍（编排裁决 D3） |
| `server/mastery.py` | **掌握与冷却的读数 + 状态机的唯一一份实现**：冷却基准（从未重做过的以录入时间起算）、比较前先归一化到 UTC、冷却门取在写 `last_attempt_at` **之前**（`cooldown_gate` 是那**一份**门）。规则本体只有一处（`step`）：`apply_attempt`（#5，追加一次重做）与 `recompute_mastery`（#6，改判后重放整段历史）都从它走——两套规则分开写迟早各判各的；`peek_step`（#6，只改错因时的**只读**读数）读的是同一份门，不改任何东西 |
| `server/amend.py` | **定点修正的唯一实现**（#6）：按 `attempt_at` 定位**那一次**既有重做（0 个 → 404、≥2 个 → 409，绝不猜），只改 `verdict`／`error_causes`，审计字段（`source`/`confidence`/`provider`/`model`/`overrode`）只有它能写。掌握**按 payload 是否改判据分流**（D10）：改判 → `mastery.recompute_mastery` 重放重算；只改错因 → 一个字都不重算，卡上 `mastery` 与 `attempts[i].note` 逐字保留 |
| `server/publicbase.py` | **对外可达地址的唯一实现**（ADR 0007 第 5 条）：`resolve_public_base`（显式优先，否则按绑定之后的 `host:port` 推导）与 `public_base_warning`（推出来的地址别的设备打不开就喊）。`Catalog.public_url` 用它拼上传页链接与将来的页锚点 |
| `server/inbox.py` | **收件目录与管道接缝**：收文件（内容哈希命名）、`POST /api/inbox/scan` 的手动扫描、`multipart/form-data` 解析。**切分（#10）的接缝**在这里：可注入 `segmenter`，不注入就报 `segmentation_not_implemented`、`blocks` 给 `null`——不许编块列表 |
| `server/static/upload.html` | 手机上传页：**一个文件**，无构建步骤、不引任何外部资源。改它不用碰 Python |
| `server/pages.py` | **页的唯一实现**（B1）：页文件读写、`page_binding`（「这张卡有没有页绑定」，含旧卡提示 vs 页↔卡对不上账的两级）、`rebind`（重切按位置重合保留绑定）、`allocate_card_id`/`assign_card_ids`（首次入库时分配 id）。#10/#12/#14/#15 **消费它，不许再写一份**——否则「重切不给已审核的卡改名」这句话不成立 |
| `server/segmentation.py` | **切分与对账的唯一实现**（B2 / #10）：模型候选块的解析与校验、三条确定性判据（题号连续性／块重叠／覆盖率）、`reconcile` 的结构化结论、`reconcile_response`（把它摊成响应形状 `checks`／`reconciliation`，建与重切共用一处映射）、`classify_resegment` 的新增／保留对照。它**消费** `pages.rebind` 与 `ink.page_ink_regions`，不重写匹配也不碰图；对账码表见 §8 的「页级对账码表」 |
| `server/ink.py` | **红笔痕迹阈值的唯一定义处 + 统计的唯一实现**（B3）：`COLORED_SATURATION_MIN`（像素级，一颗像素算不算红笔）与 `COLOR_MIN_PIXELS`（框级，一个框里几个像素才算有红笔）是**两颗回答不同问题的常量**，筛选、体检、擦除三条路径都读它们；深色掩膜的两颗（`DARK_MAX_LIGHTNESS`/`DARK_MAX_SATURATION`）也在这里。`ink_statistics` **只回答「有没有红笔、多少」**，不出任何语义字段（勾／订正由模型判，归 #12）；`cropcheck` 才是「框里有没有红笔」的判据。契约 §10.2.1 为块预留的 `ink` 键由 `page_block_reports` 产出；`page_ink_regions` 产出**整页墨迹区域**（八连通，覆盖率对账唯一的输入来源，R1） |
| `server/model_client.py` | **与角色无关的模型调用底座**（B4 / #12 抽出）：`default_transport`（标准库 HTTP，`server/` 零第三方依赖）、`extract_json`、`ModelUnavailable`、`save_run`（`runs/<stamp>-<tag>.json`，图片一律不入档）。#5 的判定角色与 #12 的抽取角色共用这一条管道——**留两份 HTTP 客户端就是两份重试/留档/失败分类** |
| `server/intake.py` | **收入决策的唯一实现**（B4 / #12）：`decide_block`（规则表：错痕迹→收／纯对勾→不收／判不准→收／没有红笔→不入库／统计读不出来→待定）、`plan_decisions`（写 `keep`+`ink`+`decision`，报出「另有 M 道…未入库」的可枚举报告）、`include_blocks`（一键补收）、`run_intake`。它**消费** `ink`（统计）与 `pages`（页文件），自己既不数像素也不造页结构 |
| `server/intake_client.py` | **抽取角色的接缝**（B4 / #12）：红笔语义的提示词与消息形状（**要看图**，与判定角色的纯文本相反）、`parse_semantics`（答不出语义是「判不准」不是调用失败）、`image_data_url`。留档 tag `redpen-semantics`；改提示词必须重跑抽取角色的考卷 |
| `server/coords.py` | **两套坐标基准的显式换算**（B6 / #14 的唯一实现）：掩膜框（裁剪图）↔ 整页框、归一化 ↔ 像素、xywh ↔ xyxy。理由：`source.bbox_*` 是**整页**坐标而 `clean.boxes_norm`／`manual` 是**裁剪图**坐标，混用**不会报错、只会静默错位**（契约 §10.2 登记在案的坑）。界面与测试都调它，不许各处现算 |
| `server/page_edit.py` | **页资源「改」动作的唯一实现**（B6 / #14）：七条动作 + `apply_edit` 写回。取舍**调 `intake.set_keep_by_human`**（收入决策只有一处实现）；歧义（拆出来的题号、丢弃块上的卡）**报出来而不是猜** |
| `server/page_api.py` | **页资源的 HTTP 翻译层**（B6 / #14，B7 / #15 补两个动作）：`PATCH`／`resegment`／`image`／`POST /api/page`（建）／`POST …/commit`（入库）都落地。它不实现规则，只翻译形状——建在 `page_create`、改在 `page_edit`、入库在 `page_commit`、三态在 `segmentation` |
| `server/page_create.py` | **页资源「建」动作的唯一实现**（B7 / #15）：照片（内容哈希前 12 位定页 id）→ 存图 + 建页文件 + 跑切分 + #11 统计 + #12 建议去留。幂等（同图重传不重跑切分、不覆盖块列表）；模型失败先用系统临时目录跑切分，成功才写进 `data/`（拒绝不留痕迹）；`as_candidates` 是切分接缝的归一化（建与重切共用） |
| `server/page_commit.py` | **页资源「入库」动作的唯一实现**（B7 / #15）：只给「收」的块发卡号、id 首次分配（`pages.assign_card_ids`）、已经生成过的块不重复生成、**先写页再建卡**（幂等：中断后重跑不会换 id）、把绑定写回页文件、报告索引重建。它不另判收不收（那是 #12）也不另分配 id（那是 #9） |
| `server/audit.py` | **落盘数据体检的唯一实现**（B7 / #15）：页↔卡**双向**对账（§8 的 B7 码表）、`CHECKS`（「我查了哪几项」）、`python3 -m server.audit`。**只读盘、不修改、可随时重跑**；页绑定与字段自检分别消费 `pages.page_binding` 与 `warnings.card_warnings`，串题判据消费 `warnings.duplicate_transcript_groups`——一条规则一份实现。退出码只认 `warning` 级发现 |
| `server/subjects.py` （**本版（前端重构 / #17）新增**） | **受控词表与大纲的唯一读取实现**（§10.4）：读 `<数据目录>/vocab/subjects.json` 与 `<数据目录>/vocab/topic-outline.seed.json`、产出 `/api/index` 的 `subjects`／`outline`（§3）、出 `stats.by_subject` 与 `stats.unclassified`（含那条不变式）、以及逐卡的 `subject_missing`（hint）／`subject_unknown`（warning）。**读不出来不是 500**，是词表级警告 + 空词表；词表没读出来时**一条 `subject_unknown` 都不发**。别的模块要科目判定只有这一个地方可消费 |
| `server/brief.py` （**本版（前端重构 / #17）新增**） | **简报的生成与数字闸门的唯一实现**（§10.5）：按窗口汇总读数 → 问 `brief` 角色 → 把 `text` 里用到的每个数字落成 `window_facts`／`history_facts`（`path` 指向本次索引）→ **逐条解出来核对**，一条对不上就**不落盘、502 `brief_unverifiable`**。落盘 `<数据目录>/briefs/<科目>-<YYYY-MM-DD>.json`，先写临时文件再原子替换 |
| `server/brief_client.py` 与 `server/segmenter_client.py` （**本版（前端重构 / #17）新增**） | 两个角色各自的**调用接缝**，照 `server/intake_client.py` / `server/judge_client.py` 的形状（提示词与消息形状按角色分家、共用 `model_client` 那条与角色无关的管道、`runs/` 留档 tag 分别是 `brief` 与 `segment`）。`brief_client` 是**纯文本**角色，`segmenter_client` **要看图**（整页照片 + 候选块）——前置一份**视觉探针**：纯文本模型收到图片不会报错，它会忽略图片、照着提示词**凭空编块**（`CONTEXT.md`「视觉探针」） |
| `server/subject_assign.py` （**本版（前端重构 / #17）新增**） | 受控词表的**显式迁移**入口（§10.4）：`python3 -m server.subject_assign --map <表.json> [--apply]`，把存量卡按人给的映射写进 `subject`。**只认人给的映射表、不按考点推**（猜错的科目看起来是对的，而没有人会去核对一个看起来对的答案）；**坏值一票否决**（表里有一条不在词表里 → `--apply` 一个字节都不写、退出码 1）；预演与 `--apply` 分开（同 `server/backfill.py` 的先例），消费 `subjects.load` 判词表，不另写一份科目判据 |

## 12.1 测试接缝

- **唯一被测的接缝是 HTTP**：`server.http.Api.handle(method, target, body, content_type) -> Response`
  与真起一个 socket 的端到端冒烟。测试不碰内部函数名，不 mock 内部协作者。
  上传（#13）就在这个接缝上测：一个自造的 `multipart/form-data` body 进去，
  信封出来、临时收件目录里多个文件出来；**不碰真照片、不占固定端口**（`port=0`）。
- 纯逻辑（冷却／排序／可判性／警告码／对外地址解析）用**假时钟 + 构造记录**喂进去测，
  不联网、不花钱、不画图——`proto/test_mastery.py` 是这份做法的先例。
- **拒绝路径也要有测试**（#13 的教训）：`POST /api/inbox` 的「一次传的全部是空文件」
  这一条只有它自己走到，写错一个变量名就退化成兜底 500，而 270 条测试照样全绿。
  所以每条会拒绝的分支都要有一条**断言形状**的测试（`code`/`reason`/`message`/`hint`/`details`
  齐、HTTP 码对、**不是** `internal_error`），而断言「没留下任何痕迹」的测试要连
  「目录都不该被建出来」一起断言。
- **页的纯逻辑**（`server/pages.py`：`page_binding`／`rebind`／`allocate_card_id`／
  `assign_card_ids`）用**构造的块列表**喂进去测，同样不联网、不画图——这是 spec #2
  「测试四层」的第 1 层，#9 的验收就落在这一层。它同时也是 HTTP 那一层的判据来源
  （`card_warnings` 里的页绑定提示由 `page_binding` 一处产出）。
- **入库与审计也在这个 HTTP 接缝上测**（#15）：建/入库各一个 multipart／无 body 的请求进去，
  信封出来、`problems/` 与 `pages/` 里的东西变/没变出来。切分是注入的假接缝（**不联网**），
  合成图用 `server/ink.py` 自己的 PNG 编码器画（零依赖）。审计是纯读的入口：
  一个数据目录进去、一份 `checked`／`findings` 出来，所以它同时是纯逻辑测试。
- **端到端一条链**（spec #2 第 5 层）在 `server/tests/test_page_pipeline.py`：
  合成一页 → 切分 → 收两道丢一道 → 生成两张卡 → 索引 → 审计 → **重切回到「保留」**，
  并断言重切与入库都没有偷偷改卡/改页（逐字节比对）。骨架卡刚入库时审计**一定**有
  「内容还没填」的 warning（题面／标准答案／擦除图），所以那条测试先把「页↔卡对账没有错误」
  与「内容缺项」分开断言，再模拟审核把内容填上，然后断言审计一条发现都没有。
- **模型上岗验收不在上面那些接缝里**（**本版（前端重构 / #17）新增**）：
  `python3 -m pytest server/tests/test_acceptance_models.py -v`。
  **换模型就要重跑验收，不过考不许上岗**（`CONTEXT.md`「验收」）——这句话要写进
  那个测试文件的头部注释里，而且它必须是一次**可执行的命令**，而不是一句愿望。
  它**需要密钥**（真的去问模型）：**没密钥时 skip，
  而不是 pass**——把「没跑成」报成「通过了」正是这个项目最怕的那种静默。
  角色的默认值与环境变量见 §1；`segmenter` 与 `brief` 两个新角色的调用接缝见 §12。
- **`segmenter` 的上岗闸门：三条都必需**（#17 §8、§33）：① **视觉探针**必须过——它防的正是
  「纯文本模型收到图片不报错、忽略图片、照着提示词**凭空编块**」；② 一份**固定照片集**上的
  **人工判定**（切分质量只有人能判，判据写进验收记录）；③ `server/segmentation.py` 的
  三条**确定性对账判据**（题号连续性／重叠／覆盖）必须**全过**。三条**都**必须过，缺一条不算上岗。
  那份固定照片集**落进仓库当夹具**，所以「换模型重跑验收」是一次可执行的命令。
  已知弱点要**显式**写进验收记录：题号连续性**依赖模型把题号报对**——报不出题号时那条判据
  只报 `question_number_missing`（hint），**不许当成通过**。
- 测试数据**自造在临时目录里**，测完即删。真实题卡只有两张、重做次数是 0，
  测试绝不碰它们（工单 #1 的第 35 条 user story）。
