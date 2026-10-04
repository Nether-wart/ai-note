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
- 数据目录默认是仓库根的 `data/`，用 `--data <dir>` 或环境变量 `AI_NOTE_DATA` 覆盖。
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
  下面的 `inbox/`（即 `--data data` → `data/inbox/`）。它**跟着 `--data` 走**：
  换了数据目录却还往仓库里的 `data/inbox` 写，就是往真数据里写。
  「往这里放一个文件」是录入的唯一入口（ADR 0007 第 4 条）。上传、扫描与上传页见 §10.3。

### 时间与 id

- 所有时刻是 ISO 8601 带偏移的字符串，原样来自题卡（`created_at` 常是 `+08:00`，
  重做时刻是 `+00:00`）。**要比较的时刻一律先归一化到 UTC**——比原始字符串会把
  `06:31Z` 排在 `09:00Z` 后面（`sort_key` 的注释里记着这个真踩过的坑）。
- 题卡 id 形如 `p-20261004-41c86b`，标题、`warnings` 与 URL 里都用它。
  路径参数只接受 `^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*$`（不许出现 `..`），
  其余一律 400（防路径穿越，见 §7.3）。

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
| `level` | `"warning"` \| `"hint"` | 严重度。`"warning"` = 这里有东西不对，要看；`"hint"` = 提示级（例如「旧卡缺页绑定」，旧数据不该因为新结构变成脏数据）。**级别只有服务能定**，界面不许自行降级。这个键**总是显式发出来**（v0 现有的码一律 `"warning"`，`hint` 级的 `page_binding_missing` 归 #9）——有默认值却不出现在响应里，下游只能靠猜 |

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
    "auto_judge_ineligible": 1
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

逐卡（出现在 `Problem.warnings` 里。v0 的码 `level` 全是 `"warning"`，且**每个警告对象都显式带这个键**；下表中标注 `hint` 的那条归 #9 落地）：

| `code` | 触发 | `message` |
|---|---|---|
| `standard_answer_missing` | 标准答案为空 | 标准答案为空 → 不能走自动判定，只能人工确认 |
| `standard_answer_not_choice_letter` | 选择题的标准答案不是单个选项字母 | 选择题的标准答案不是选项字母：'…' |
| `standard_answer_choice_letter_on_non_choice` | 非选择题的标准答案是单个字母 | 题型是解答，标准答案却只有一个字母 'A' ——像是把选择题的答案填到这道题上了 |
| `original_answer_choice_letter_on_non_choice` | 非选择题的原答是单个字母 | 题型是解答，原答却是一个选项字母 'A' |
| `original_answer_equals_standard_answer` | 原答与标准答案相同 | 原答与标准答案相同（都是 'A'）→ 这是错题本，录进来的是做错的题；两者相同通常意味着订正被当成了原答 |
| `topics_empty` | 考点为空 | 考点为空 → 考点是检索入口 |
| `reviewed_but_incomplete` | 已审核但标准答案或考点为空 | 已标为已审核，但标准答案或考点是空的 |
| `no_clean_image` | 没有擦除手写后的题面图 | 没有擦除手写后的题面图 → 不进屏幕重做，也不进重做纸 |
| `clean_image_file_missing` | 卡里记了 `clean_image`，文件不在 | 卡里记了擦除图 …，但文件不在 → 屏幕重做会拿到一个 404 |
| `original_image_file_missing` | 卡里记了题面图，文件不在 | 同上 |
| `page_binding_missing` （`level: "hint"`） | 卡没有页文件绑定（`source.page_image` 缺，或按 §10.2 推出来的页文件不在） | **落地于 #9**（B1）。#9 验收第 2 条：旧卡缺页绑定要报**提示**而不是错误——旧数据不该因为新结构变成脏数据。回填（`python3 -m server.backfill --apply`）之后这条消失 |
| `page_binding_lost` （`level: "warning"`，**B1 新增**） | 页文件**在**，却读不了／不是 JSON 对象，或里面没有任何块绑定这张卡 | 页实体已经在场却对不上账，这是矛盾不是旧数据，所以要**警告**。与上一条同一个判据（`server/pages.py: page_binding`），只有级别不同——**两种缺绑定不许混成一个级别**（#9 验收 2、#15 在它上面扩展） |

索引级警告（出现在 `data.warnings` 与信封 `warnings` 里，`level` 全是 `"warning"`）：

| `code` | 触发 | 为什么值得单独响 |
|---|---|---|
| `duplicate_transcript` | 两张卡的题干**逐字相同** | `CONTEXT.md`「串题」：两道不同的题不可能有同一段题干，这条**没有例外**。第八轮那次污染就是这么被抓出来的 |
| `problem_id_mismatch` | 文件名与卡内 `id` 不一致 | 改名事故或复制粘贴事故 |
| `public_base_not_reachable` | 服务绑在通配地址（说明想让别的设备连），而 `data.server.public_base` 是通配／环回地址 | ADR 0007 第 5 条那处**已查明的债**的可见化：绑 `0.0.0.0` 时印 `http://0.0.0.0:8765`，手机打不开。默认只监听本机（`127.0.0.1` + 环回地址）**不**报这条——那是设计如此，报它会训练人忽略警告 |

收件目录（#13；出现在 §10.3 那两个端点的 `warnings` 里）：

| `code` | `level` | 触发 | `message` |
|---|---|---|---|
| `segmentation_not_implemented` | `warning` | **有页要处理**，但切分（#10）还没接上 | 切分尚未实现（#10）：照片已经收进收件目录，但这次没有块列表、没有红笔统计，也没有生成任何题卡（入库归 #9/#15） |
| `already_in_inbox` | `hint` | 同一内容已经收过（文件名取内容哈希，同步盘会把同一张再同步一遍） | `<原名>` 的内容收件目录里已经有了（`<stored_as>`），没有重复写一份 |
| `unexpected_file_type` | `warning` | 后缀不在已知照片后缀里 | `<原名>` 的后缀不是已知的照片后缀（…）→ 收下了，但请确认这是照片 |
| `inbox_created` | `hint` | 扫描时收件目录原来不存在、刚建了一个 | 收件目录原来不存在，刚建了一个：`<路径>` |

**跳过码表（出现在 `skipped` 里，不是 `warnings`）**——这一档比警告重：**记录根本没建出来**。

| `code` | 触发 | `message` |
|---|---|---|
| `problem_file_unreadable` | 文件读不了（JSON 解析失败、权限、编码） | `<路径> 读不了：<异常类名>: <说明>` |
| `problem_not_dict` | 文件里不是 JSON 对象 | `<路径> 不是一个 JSON 对象` |
| `problem_missing_field` | 缺 `problem.transcript` 这类必需字段 | `<路径> 缺 problem.transcript，建不出这条记录` |
| `inbox_part_empty` | 一次上传里某个 part 是空文件（#13） | 第 N 个 part（`<原名>`）是空的，没有收进收件目录 |

`skipped` 非空时 `stats.problems_skipped` 也非零，且 `count` **不含**被跳过的那些
——「少了一张卡」必须是一个看得见的数字，不是一个安静的空位。

## 9. 错误码表（v0）

| `code` | HTTP | 场景 |
|---|---|---|
| `bad_request` | 400 | 路径参数、查询参数、body 不合法。`details` 里必须点名**是哪个参数、值是什么、允许什么**，`hint` 里给**规则本身**（例如 id 的字符集）。**输入错不许用 404 或 500 表达** |
| `not_found` | 404 | 没有这条路由 / 没有这道题 / 没有这张图。路由级 404 的 `message` 会指出**这条路径像哪条已知路由**；命中 §10 的预留命名空间时另带 `details.reserved`，明说「预留、还没实现」——含糊的 404 会让人以为是打错了字 |
| `method_not_allowed` | 405 | 端点收到它不接受的方法（只读端点收到 POST、`/api/inbox*` 收到 GET…）。`details.allowed` 列出允许的方法，**`OPTIONS` 永远在列表里**（预检） |
| `internal_error` | 500 | 服务自己出错。`message` 带异常类名，`hint` 指向服务日志。**不允许用 500 表达「输入不对」** |
| `payload_too_large` | **413** | 一次上传的 body 超过上限（默认 32MB，见 §10.3）。`details` 给 `{param, value, max}`。**判在读 body 之前**：上限要在声明长度上就判掉，读一个百 MB 的 body 再拒绝不是拒绝。这个响应之后连接会关闭（body 没读完，keep-alive 会串味） |
| `not_auto_judgeable` | **422** | **#5，已实现**：`reason` ∈ §6 的三个码之一，`message` 就是那句中文原话，`warnings[]` 带该卡的自检警告。**不是 400，更不是 500**（编排裁决 D1） |
| `model_unavailable` | **502** | **#5，已实现**：判定角色调用失败（网络／超时／缺密钥）。同一个信封，`reason = "model_unavailable"`。**这一次重做不留下任何记录**——「我们没能问成」不是「看不清」，记成看不清会凭空造出一条没发生过的重做，并静默重置冷却。界面可以直接重试 |
| `ambiguous_attempt_at` | **409** | **#6，已实现**：定点修正的 `attempt_at` 定位到**不止一次**重做（同一秒里做了两次）。`details.candidates` 列出命中的索引。**不是 404**：那几次确实存在，只是这个参数区分不了它们；挑一个就是「猜」 |

`not_found` 下的 `reason` 取值（都是 404，`code` 不变）：没有这道题（`reason == "not_found"`）、
没有**那一次重做**（`reason == "attempt_not_found"`，#6，`details.available` 列出实际有哪些时刻）、
没有这张图（`reason == "not_found"`）。

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
| §10.2 `/api/page*` | 形状已落定（#9）；四个动作未实现（#10 #12 #14） |
| §10.3 收件目录与手机上传页 | **已实现**（#13） |

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

**掌握只能重算**（`server/mastery.recompute_mastery`）：改的是历史里某一次（甚至不是
最后一次）时，把卡里 `attempts` 从头按既定规则**重放**一遍。状态机对
「`created_at` + 一串 (at, verdict)」是确定的纯函数，所以重放得到的读数**就是**
「这条判定从一开始就是这样」时该有的读数；就地打补丁会让后面几次的 `credited`
仍按旧判定算。重放用的是**同一个冷却门**（先算门、后写 `last_attempt_at`，见下），
不允许定点修正另写一套。

于是定点修正返回的 `mastery` **分两层**（改的是最后一次时两者重合）：

- `state`／`streak`／`last_attempt_at` = **重放后卡级的终态**（与 `GET /api/index` 一致）；
- `cooling`／`credited`／`gap_days`／`note` = **被改那一次**在重放里的读数。

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
`run_id` 就是那份档的标识，200 时一并返回；`runs/` 不进仓库（`.gitignore` 里已有）。
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
  "origin": {                      // 来源信息（spec #2：「第几页、哪张卷子」）
    "original_file": "2.png",      // 上传时的原始文件名（来自 source.original_file）
    "sheet": null,                 // 哪张卷子；未填为 null
    "page_number": null            // 第几页；未填为 null
  },
  "blocks": [
    {
      "id": "b1",                                   // 页内唯一、稳定的块 id
      "bbox_norm": [0.02, 0.12, 0.76, 0.28],        // 规范基准：整页归一化 xywh
      "bbox_px": [3, 20, 541, 79],                  // 只作交叉验证：整页像素 xyxy
      "card_id": "p-20261004-41c86b",              // 绑定；null = 还没入库
      "keep": true                                   // 去留：true 收 / false 丢弃 / null 待定
    }
  ]
}
```

**两个边界的形状不一样，别当成同一件事**：

| 字段 | 形状 | 来源 |
|---|---|---|
| `bbox_norm` | `[x, y, w, h]`（**xywh**），相对**整页**归一化、原点左上 | 与 `source.bbox_norm` **同一语义**（`proto/slice.py:281`） |
| `bbox_px` | `[x0, y0, x1, y1]`（**xyxy**），整页像素 | `crop_problem(pad=0.015)` 加 1.5% pad 又裁到页边界的结果（`proto/slice.py:544-555`） |

回填时 `bbox_px` **照抄盘上的原值**，不用 `bbox_norm` 重算。
下游扩展键**先占名字**（B1 不写）：`question_no`（#10）、`ink`（#11 红笔统计）、`type`（#14）。

**题卡上的 `source.{page_image,bbox_norm,bbox_px,original_file}` 保留不动**：
绑定关系只记在页文件里（spec #2 原话），所以「这张卡有没有页绑定」是从页文件推出来的，
卡上不加新字段。

#### 10.2.2 模块与命令（B1 落地）

| 位置 | 是什么 |
|---|---|
| `server/pages.py` | 页文件的读写，以及两处**唯一实现**：`page_binding(catalog, card)`（这张卡有没有页绑定，`card_warnings` 与 #15 的审计都消费它）、`rebind(old, new)`（重切时按位置重合保留绑定） |
| `server/pages.py: allocate_card_id` / `assign_card_ids` | 题卡 id **首次入库时分配**：形状沿用 `p-<YYYYMMDD>-<6hex>`，日期是**入库日**（不是拍照日）；候选由**块的身份**（`<页 id>#<块 id>`）决定、唯一性由已在库的 id 集合保证 → 一页多块各得一个互不相同的 id（#9 验收 3） |
| `server/pages.py: rebind` | 重切对账的匹配：位置重合度 = **IoU**，具名常量 `MATCH_IOU = 0.5`（口径的最终裁决在 #10）。一对一贪心；配上的新块继承旧块的 `card_id` 与 `keep`（人动过的两样）；配不上的 `card_id = null`（分配在入库那一刻）。**只给事实、不写盘不写题卡**；对照事实在并排的 `matches` 里，不写进块 |
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
| 页文件的 `<hash12>` 取自 `page_image` 文件名；块边界以归一化坐标为规范基准 | 编排裁决 D5。照片同目录、与照片同寿命；归一化坐标不随重编码失效 |
| `deps`：`server/` 零第三方依赖（只用标准库 `http.server`） | 十轮实测的资产在 Python 里，但**这一层没有图像与模型的活**；少一个依赖就少一次打包与升级的谈判 |
| 对外地址**显式可配**（`--public-base` / `AI_NOTE_PUBLIC_BASE`），推导出来的打不开就报 `public_base_not_reachable` | ADR 0007 第 5 条那处已查明的债：原型从 `--host` 推，绑 `0.0.0.0` 就印 `http://0.0.0.0:8765`。默认推导仍然保留（本机自用最省事），但**必须能覆盖**，且推不出来时**要喊**（不许静默）。`Catalog.public_url` 是拼绝对地址的唯一入口——页锚点将来走同一个地方 |
| 收件目录的文件名取**内容哈希**（`sha256[:12]` + 后缀） | 同步盘会把同一张照片再同步一遍、手机上传页可能点两次。哈希命名让「同内容＝同一个文件」天然成立，也让将来页文件的 `<hash12>` 口径一致。原名只作显示 |
| `blocks: null` 表示「还没切」，与 `[]`（切出 0 块）区分开 | #13 交付时切分（#10）还没做。用空列表冒充会让界面看起来能跑——ADR 0007 第 6 条要的是「我没做什么」说得出来 |
| `POST /api/inbox` 是 v0 唯一的写端点，且只往收件目录写**新**文件 | ADR 0007 第 4 条把「放一个文件」定为录入的唯一入口；写新文件不是改已有数据，所以 §3 的 `read_only: true` 仍然成立，`read_only_note` 把那句话解释清楚 |
| 上传 body 上限 **413**，且判在**读 body 之前** | 服务将来要经局域网暴露给手机（#13）。读一个百 MB 的 body 再拒绝不是拒绝；这个响应之后关连接，因为 body 没读完，keep-alive 会串味 |

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

## 12. 模块角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `server/autojudge.py` | **「能不能自动判定」的唯一实现**：三个拒绝理由与它们的优先级。`#5` 的写端点**必须调用它**，不许重写；界面只显示 `reason_text`，不许自己再判一遍（编排裁决 D3） |
| `server/mastery.py` | **掌握与冷却的读数 + 状态机的唯一一份实现**：冷却基准（从未重做过的以录入时间起算）、比较前先归一化到 UTC、冷却门取在写 `last_attempt_at` **之前**。规则本体只有一处（`step`）：`apply_attempt`（#5，追加一次重做）与 `recompute_mastery`（#6，定点修正后重放整段历史）都从它走——两套规则分开写迟早各判各的 |
| `server/amend.py` | **定点修正的唯一实现**（#6）：按 `attempt_at` 定位**那一次**既有重做（0 个 → 404、≥2 个 → 409，绝不猜），只改 `verdict`／`error_causes`，掌握交给 `mastery.recompute_mastery`，审计字段（`source`/`confidence`/`provider`/`model`/`overrode`）只有它能写 |
| `server/publicbase.py` | **对外可达地址的唯一实现**（ADR 0007 第 5 条）：`resolve_public_base`（显式优先，否则按绑定之后的 `host:port` 推导）与 `public_base_warning`（推出来的地址别的设备打不开就喊）。`Catalog.public_url` 用它拼上传页链接与将来的页锚点 |
| `server/inbox.py` | **收件目录与管道接缝**：收文件（内容哈希命名）、`POST /api/inbox/scan` 的手动扫描、`multipart/form-data` 解析。**切分（#10）的接缝**在这里：可注入 `segmenter`，不注入就报 `segmentation_not_implemented`、`blocks` 给 `null`——不许编块列表 |
| `server/static/upload.html` | 手机上传页：**一个文件**，无构建步骤、不引任何外部资源。改它不用碰 Python |
| `server/pages.py` | **页的唯一实现**（B1）：页文件读写、`page_binding`（「这张卡有没有页绑定」，含旧卡提示 vs 页↔卡对不上账的两级）、`rebind`（重切按位置重合保留绑定）、`allocate_card_id`/`assign_card_ids`（首次入库时分配 id）。#10/#12/#14/#15 **消费它，不许再写一份**——否则「重切不给已审核的卡改名」这句话不成立 |
| `server/ink.py` | **红笔痕迹阈值的唯一定义处 + 统计的唯一实现**（B3）：`COLORED_SATURATION_MIN`（像素级，一颗像素算不算红笔）与 `COLOR_MIN_PIXELS`（框级，一个框里几个像素才算有红笔）是**两颗回答不同问题的常量**，筛选、体检、擦除三条路径都读它们；深色掩膜的两颗（`DARK_MAX_LIGHTNESS`/`DARK_MAX_SATURATION`）也在这里。`ink_statistics` **只回答「有没有红笔、多少」**，不出任何语义字段（勾／订正由模型判，归 #12）；`cropcheck` 才是「框里有没有红笔」的判据。契约 §10.2.1 为块预留的 `ink` 键由 `page_block_reports` 产出 |

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
- 测试数据**自造在临时目录里**，测完即删。真实题卡只有两张、重做次数是 0，
  测试绝不碰它们（工单 #1 的第 35 条 user story）。
