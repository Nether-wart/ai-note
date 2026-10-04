# HTTP API 契约 v0（只读）

> 状态：**v0，只读**。这份文档是后端与界面之间唯一必须跨得过去的东西
> （[ADR 0007](../adr/0007-redesign-the-backend-contract-first.md) 第 1 条：契约优先）。
> 实现落在 `server/`，界面落在 `site/`。将来换语言重写实现、或把服务塞进 Tauri 进程，
> 换的是实现，不换这份契约。
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
| 有 | 每个响应带 `warnings[]`，以及「我跳过了什么」的 `skipped[]`（ADR 0007 第 6 条：不许静默） |
| 有 | 屏幕重做的硬闸门读数（有擦除图 **且** 可自动判定）与「另有 N/M 道进不来」的两个显式数字（§6.1） |
| 有 | 「这道题能不能走自动判定」的**唯一一份**实现（三拒绝理由，见 §6）——#5 直接复用，不许另写 |
| 无 | 任何写端点。`POST /api/attempt/<pid>` 的形状在 §10.1 预留，实现归 #5 |
| 无 | 页面渲染。服务不出 HTML（ADR 0007 第 2 条），只出 JSON 与静态图片 |
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
  v0 用不到，但配置项先摆在 `--public-base`，默认等于基址。使用它的端点在 #13。

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

`message` 是**给人看的完整句子**，不是错误码的复述——界面照原话显示，不改写、不吞掉。
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
    "basis": "in_default_list",
    "ready": 0,
    "blocked_total": 0,
    "not_auto_judgeable": { "count": 0, "by_reason": [] },
    "no_clean_image": {
      "count": 0,
      "ids": [],
      "message": "另有 0 道因缺少擦除手写后的题面图不能进屏幕重做"
    }
  },
  "warnings": []
}
```

- `built_at`：本次现算的时刻（v0 不落盘，所以它同时是「这份数据有多新」）。
- `problems`：**已经排好序**。界面按原样渲染即可，不许再排一次
  （排序规则只有一份实现，见 §4）。
- `stats`：从同一批 `problems` 一趟算出来的计数。它是**便利读数，不是第二个真源**：
  有任何不一致，以 `problems` 为准（测试断言两者一致）。
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
| `warnings` | Warning[] | 派生 | 这张卡自己的自检结果（§7.2 码表） |

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

`at` 在摘要里截到日期（`YYYY-MM-DD`）；完整时刻见 §5 的 `attempts_detail`。

- `source` 的取值**只有两个机器可读的值**：`"auto"`（自动判定）与 `"human"`（人工确认），
  中文渲染另走 `source_cn`（编排裁决 D2，沿用原型的取值）。改判（#6）后 `source` 变成
  `"human"`，原来那条判定留在 `overrode` 里——**「这条判定原本是谁给的」永远可查**。
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

界面**不重新实现**这套规则：它拿 `in_default_list` 与 `sort_key` 当**读数**用。
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
| `attempts_detail` | **全部**重做记录（不是最近 6 条），原始形状：`{at,channel,verdict,source,confidence,error_causes,note,overrode?}` |
| `source` | 这张卡从哪来：整页照片、归一化与像素边界、原始文件名。**没有就是 `null`**（存量或手工录入的卡） |
| `clean` | 擦除手写这一趟的产物读数：方法、掩膜框、像素统计、体检。没有就是 `null` |
| `provenance` | 是哪次模型调用造出这张卡的（角色／模型／当时的警告／逐字段置信度）。没有就是 `null` |

**「超集」是一条硬承诺**：列表页与详情页对同一道题说不同的话，是这个项目已经出过的
那类事故（界面把第一题的文字写进了第二题）。所以两者由**同一个**记录构造函数产出，
测试逐字段断言 `index.problems[i] ⊂ problem(j)`。

### 5.1 失败形状

| 情况 | HTTP | `error` |
|---|---|---|
| 没有这张卡 | 404 | `{code:"not_found", message:"没有这道题：p-xxx", hint:"GET /api/index 看有哪些", details:{what:"problem", id:"p-xxx"}}` |
| id 非法（含 `/`、`..`、空） | 400 | `{code:"bad_request", message:"题卡 id 非法：'../../etc/passwd'", hint:"id 只允许字母、数字、点、下划线与连字符", details:{param:"pid", value:"../../etc/passwd"}}` |
| 用错方法（POST 到这个只读端点） | 405 | `{code:"method_not_allowed", message:"...", details:{allowed:["GET","OPTIONS"]}}` |

## 6. 能不能走自动判定：一处实现，三个拒绝理由

`auto_judge` 是请求一个**服务端读数**，不是客户端自己算：

```jsonc
{ "eligible": true,  "reason": null, "reason_text": null }
{ "eligible": false, "reason": "solution_type",
  "reason_text": "解答题只能人工确认：过程题在屏幕上敲不出过程" }
```

三个理由，**按这个优先级依次判**（同时成立时取前面的那个，保证同一张卡永远给同一个答案）：

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

索引级汇总（`data.screen_redo`，编排裁决 D3/D4）：

```jsonc
{
  "basis": "in_default_list",      // 候选总体：默认打印清单（未毕业且已脱离冷却）
  "ready": 1,                      // 这个总体里真正能进屏幕重做的
  "blocked_total": 3,              // 至少有一个 blocker 的卡数
  "not_auto_judgeable": {          // 「另有 N 道不能自动判定」的那个 N
    "count": 2,
    "by_reason": [
      { "reason": "solution_type", "reason_text": "解答题只能人工确认：…",
        "count": 1, "ids": ["p-…"] },
      { "reason": "unreviewed", "reason_text": "这道题还没审核 → …",
        "count": 1, "ids": ["p-…"] }
    ]
  },
  "no_clean_image": {              // 「另有 M 道缺擦除图」的那个 M
    "count": 2, "ids": ["p-…", "p-…"],
    "message": "另有 2 道因缺少擦除手写后的题面图不能进屏幕重做"
  }
}
```

三条口径，写死在契约里免得 #7 与界面各算各的：

1. **候选总体是默认打印清单**（`basis`），不是「全部题卡」。一张冷却中的解答题本来就不在
   队列里，它不是「因为不能判定才没进」——把它算进 N，页头那句话就在撒谎。
2. **N 与 M 可以重叠**：一张既无擦除图、又是解答题的卡同时出现在两个列表里，
   因为两句话都真的成立。`blocked_total` 是**去重**的卡数，所以允许 `N + M > blocked_total`。
3. **真源是每张卡的 `screen_redo.blockers`**；`by_reason` 与 M 只是从同一批记录一趟算出来的
   便利读数。有任何一个数字对不上，以 `problems` 为准。

## 7. `GET /api/problem/<pid>/image/<kind>` —— 读图片

`kind` ∈ `original`（原题裁剪图）｜`clean`（**擦除手写后的题面图**）｜`mask`（手写掩膜）。

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
| `kind` 不在枚举里 | 400 | `{code:"bad_request", message:"图片类型非法：'thumb'", details:{param:"kind", value:"thumb", allowed:["original","clean","mask"]}}` |
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

`code` 稳定；`message` 是给人看的原话，允许打磨。**每一条码都对应一个会喊的检查**
（ADR 0007 第 6 条：每个由人填写的字段都要有一个会喊的检查）。

逐卡（出现在 `Problem.warnings` 里）：

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

索引级（出现在 `data.warnings` 与信封 `warnings` 里）：

| `code` | 触发 | 为什么值得单独响 |
|---|---|---|
| `duplicate_transcript` | 两张卡的题干**逐字相同** | `CONTEXT.md`「串题」：两道不同的题不可能有同一段题干，这条**没有例外**。第八轮那次污染就是这么被抓出来的 |
| `problem_id_mismatch` | 文件名与卡内 `id` 不一致 | 改名事故或复制粘贴事故 |
| `problem_not_dict` / `problem_missing_field` | 卡不是对象 / 缺 `problem.transcript` 这类必需字段 | 建不出这条记录，进 `skipped`，同时响一声 |

## 9. 错误码表（v0）

| `code` | HTTP | 场景 |
|---|---|---|
| `bad_request` | 400 | 路径参数、查询参数、body 不合法。`details` 里必须点名**是哪个参数、值是什么、允许什么** |
| `not_found` | 404 | 没有这条路由 / 没有这道题 / 没有这张图。路由级 404 的 `message` 会指出**这条路径像哪条已知路由**，避免手打错一个字母变成静默的 404 |
| `method_not_allowed` | 405 | 只读端点收到 POST/PUT/DELETE。`details.allowed` 列出允许的方法 |
| `internal_error` | 500 | 服务自己出错。`message` 带异常类名，`hint` 指向服务日志。**不允许用 500 表达「输入不对」** |
| `not_auto_judgeable` | **422** | **v0 预留，未实现**（#5）：`reason` ∈ §6 的三个码之一，`message` 就是那句中文原话，`warnings[]` 带该卡的自检警告。**不是 400，更不是 500**（编排裁决 D1） |
| `model_unavailable` | **502** | **v0 预留，未实现**（#5）：判定角色调用失败（网络／超时／缺密钥）。同一个信封，`reason = "model_unavailable"`。**这一次重做不留下任何记录**——「我们没能问成」不是「看不清」，记成看不清会凭空造出一条没发生过的重做，并静默重置冷却。界面可以直接重试 |

补充硬规则（编排裁决 D1）：

- **拒绝用 422，不用 400**：请求本身是合法的（题存在、字段对），只是这道题**不能**自动判定。
  400 留给「你参数写错了」。
- **绝不允许**裸 500、空响应体、`text/plain` 的异常回溯、`sys.exit`。
  v0 里那唯一一条兜底 500（`internal_error`）也走同一个 JSON 信封，`message` 带异常类名，
  `hint` 明说「输入不合法应该是 400 而不是 500」。

## 10. v0 预留（**未实现**，形状先钉住）

写端点一个都不实现（工单 #3 明写「不做任何写端点」）。但形状必须先钉住，
否则 #5 #9 #13 会各自发明一套。

### 10.1 `POST /api/attempt/<pid>` —— 归属 #5、#6

三种形态：

```jsonc
{ "channel": "screen", "answer": "<作答文本>" }                 // 自动判定
{ "channel": "paper", "verdict": "…", "at": "…", "error_causes": ["…"] }  // 人工确认（行为不变）
{ "attempt_at": "…", "error_causes": ["…"], "verdict": "…" }     // 定点修正（#6）
```

| 结果 | HTTP | `error` |
|---|---|---|
| 成功 | 200 | `data: {attempt: {…完整那一次…}, mastery: {streak,state,cooling,credited,note}, index_rebuilt_at: "…"}` |
| 三个拒绝理由 | **422** | `{code:"not_auto_judgeable", reason:"solution_type"\|"no_standard_answer"\|"unreviewed", message:"<autojudge 那句原话>", details:{id}, }` + `warnings[]` = 该卡的自检警告 |
| 模型调用失败 | **502** | `{code:"model_unavailable", reason:"model_unavailable", message:"…", hint:"可以直接重试；这一次没有留下任何记录"}` |
| `channel`／`verdict` 取值非法 | 400 | `{code:"bad_request", details:{param,value,allowed}}` |

`reason` 与 `message` **必须**取自 `server/autojudge.py` 的同一份实现（§6）——
#5 的派发简报已经写明「必须调用它、不许重写」。

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

### 10.3 收件目录 —— 归属 #13

`/api/inbox*` 命名空间预留（往收件目录放一个文件、上传页、目录监视的手动等价入口）。
形状由那份工单定。

**命名空间先占住**：`/api/attempt/*`、`/api/page*`、`/api/inbox*`。
`GET` 到这些路径现在返回 404，且 `message` 明说「这条路由是预留的、v0 还没实现」，
并带 `details.reserved`——含糊的 404 会让人以为是打错了字。

## 11. 契约决定与偏离记录

| 决定 | 为什么 |
|---|---|
| 图片端点是二进制字节，是「全部 JSON」的唯一例外 | §7.1。已在 issue #3 评论留痕 |
| `warnings` 从 proto 的 `list[str]` 变成 `Warning[]`（`{code,message,id}`） | 界面要照原话显示，测试要按 `code` 断言。字符串列表两者都做不到。口径（该检查什么）全盘继承 proto，只是形状结构化了 |
| 新增 `skipped[]` | ADR 0007 第 6 条要的就是「我没做什么」。索引建不出来一条卡时，光有 `warnings` 说不清「少了一张」 |
| 索引**不落盘**，每次请求现算 | v0 只读，写路径归写端点。现算 = 永远没有陈旧索引。落盘与否**不改契约**（客户端看不出区别） |
| `Problem` 在列表与详情里由同一个构造函数产出，详情是其超集 | §5 的硬承诺，防串题那类事故 |
| 加 CORS（`Access-Control-Allow-Origin: *` 与 `OPTIONS`） | `site/` 在 :3000，服务在 :8765，是跨源。没有它列表页一行数据都拿不到。服务只监听本机（ADR 0003），`*` 的暴露面就是本机 |
| 三个端点就三个端点，不加 `/api/queue`、`/api/health` | 队列（默认打印清单）由 §4 的读数与 §6.1 的 `screen_redo` 汇总额外提供，界面不重算规则；加第四个端点等于把同一套规则再实现一遍 |
| 拒绝用 **422**、模型失败用 **502**，绝不用裸 500 | 编排裁决 D1。请求合法而这道题不能判定 ≠ 参数写错；上游挂了 ≠ 服务端 bug |
| `source` 是 `auto`/`human` 两个机器取值，中文走 `source_cn`；`provider` 与 `model` 分开 | 编排裁决 D2。把中文写回 `source` 会让「有多少判定是机器给的」这类体检变成字符串匹配 |
| 进屏幕重做 = 有擦除图 **且** 可自动判定；缺擦除图不回退原图，但要把道数报出来 | 编排裁决 D4。原型那条 `or`（`proto/server.py:235`）会让「已审核但无擦除图」的卡渲染成 `<img src="None">`；而静默丢掉又是这个项目最怕的失败 |
| 页文件的 `<hash12>` 取自 `page_image` 文件名；块边界以归一化坐标为规范基准 | 编排裁决 D5。照片同目录、与照片同寿命；归一化坐标不随重编码失效 |
| `deps`：`server/` 零第三方依赖（只用标准库 `http.server`） | 十轮实测的资产在 Python 里，但**这一层没有图像与模型的活**；少一个依赖就少一次打包与升级的谈判 |

## 12. 模块角色（下游一眼要看到的两件事）

| 模块 | 角色 |
|---|---|
| `server/autojudge.py` | **「能不能自动判定」的唯一实现**：三个拒绝理由与它们的优先级。`#5` 的写端点**必须调用它**，不许重写；界面只显示 `reason_text`，不许自己再判一遍（编排裁决 D3） |
| `server/mastery.py` | **掌握与冷却的只读读数**：冷却基准（从未重做过的以录入时间起算）与「比较前先归一化到 UTC」。写状态机（`apply_attempt`）归 `#5`，也在这一处实现，免得两套规则 |

## 12.1 测试接缝

- **唯一被测的接缝是 HTTP**：`server.http.Api.handle(method, target) -> Response`
  与真起一个 socket 的端到端冒烟。测试不碰内部函数名，不 mock 内部协作者。
- 纯逻辑（冷却／排序／可判性／警告码）用**假时钟 + 构造记录**喂进去测，不联网、不花钱、
  不画图——`proto/test_mastery.py` 是这份做法的先例。
- 测试数据**自造在临时目录里**，测完即删。真实题卡只有两张、重做次数是 0，
  测试绝不碰它们（工单 #1 的第 35 条 user story）。
