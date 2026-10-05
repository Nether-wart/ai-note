# `site/` —— 界面（Docusaurus 静态站，ADR 0006）

**它不碰数据、不碰模型、不碰状态机**：只把 `server/` 给的 JSON 画出来。

## 信息架构（#17：索引栏在运行时自绘）

```
首页总览 /                       跨科目总览（每科目一行读数）
└─ 科目  /subject?name=<科目>&view=<栏>
   ├─ 简报      模型写的近况；按需生成，数字要过闸门
   ├─ 细则      该科目全部错题，按录入时间由新到老
   ├─ 考点大纲  章 → 节 → 点
   └─ 今日重做  默认打印清单；队列进 /redo?queue=…&i=…
└─ 未归类      /subject?view=detail（没定科目的卡全兜住，道数写在栏上）
阅读页 /problem?pid=…           题意、正解与标准答案、原解与订正、重做历史
重做页 /redo?queue=…&i=…        （契约没变；见下）
切分与录入 /split?page=<页 id>   工作台：照片 → 切分 → 人工调整 → 入库
```

`docs: false` 不变：索引是**运行时数据**，进构建产物就与 ADR 0001 冲突，所以侧栏
由 `src/components/Sidebar.js` 自绘（类名与颜色向 Docusaurus 的 `menu` / `--ifm-*` 看齐，
不引入新依赖）。索引只在 `src/components/Shell.js` 的 `ShellProvider` 里取**一次**
（`GET /api/index`），由 `src/theme/Root.js` 挂在路由之上——页面是**用外壳套住自己**的，
provider 若写在外壳里，页面就读不到（context 只向下流；实测过一次）。

| 文件 | 是什么 |
|---|---|
| `src/components/Shell.js` | `ShellProvider`（取一次索引 + 全屏工作台的开关）与 `Shell`（左侧栏 + 主内容区 + 索引横幅） |
| `src/components/Sidebar.js` | 运行时自绘的科目索引栏（`sidebarTree()`） |
| `src/components/Workbench.js` | 全屏工作台：上传／建页、拖边界、新增、删除、改去留与题号题型、重置为预设、入库 |
| `src/theme/Root.js` / `src/theme/Layout/index.js` | 两个 swizzle：provider 在路由之上，外壳在每一页之外 |
| `src/pages/index.js` / `subject.js` / `problem.js` | 总览／科目下四栏／阅读页 |


## 跑起来

需要两个进程（数据由服务给，界面在运行时取）：

```bash
# 1) 只读服务（默认 127.0.0.1:8765，数据目录默认仓库根的 data/）
python3 -m server.app --data ../data --port 8765

# 2) 界面（默认 http://127.0.0.1:3000）
AI_NOTE_API=http://127.0.0.1:8765 npm run start -- --port 3000 --host 127.0.0.1
```

依赖装在本地缓存里，别让 npm 写到仓库外：`npm_config_cache="$PWD/../.npm-cache" npm install`。

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `AI_NOTE_API` | `http://127.0.0.1:8765` | 只读服务的地址。**构建期**写进 `customFields.apiBase`，由浏览器在运行时使用 |
| `AI_NOTE_BASE_URL` | `/` | 站点基路径。打包成 Tauri 时给 `./`（本地文件加载要相对路径） |
| `AI_NOTE_SITE_URL` | `http://127.0.0.1:3000` | 站点自身地址（`url`） |

## 两条架构约束（写在配置与代码里，不是写在文档里）

1. **数据不进构建产物**（ADR 0001）。索引在 `useEffect` 里 fetch，
   构建期只渲染「正在从服务读索引…」。所以录入一道题**不会**触发站点重建，
   构建产物里也没有任何一道题的内容。
   自查：`grep -rl "p-2026" build/` 必须为空（#17 这一趟也验过：为空；
   连 `routes.js` 里那个示例 id 都改成了 `<题卡 id>`，因为它会跟着打包进产物）。
2. **Tauri 兼容**（ADR 0006）：纯静态 bundle、不做 SSR、无服务端渲染依赖。

## 界面纪律

- **只显示擦除手写后的题面图**（`images.clean`）。没有就明说「没有擦除图，也不回退到原图」，
  绝不回退到 `images.original`（裁决 D4）。原图只有一个**显式、带标记的动作**能打开：
  阅读页那个开关。别处一处都不许回退。
- **每题都标通道**（屏幕可做／只能纸上 + 第一条阻塞原话）。少一道题看起来就像 bug。
- **答案只在阅读页出现**（#17 §5）：总览、细则、今日重做都不印正解／标准答案／原答／订正。
- **`warnings[]` 与 `skipped[]` 必须显示**（索引横幅、工作台的每一次回执），
  服务给的 `message` **照原话显示**，界面不自己再拼一遍（拼了就会出现两句几乎一样的话——
  里程碑二第一次真实渲染就撞上了）。
- **不许静默**：工作台的 `blocks_removed` / `blocks_added`（含 0）、入库的四类结果
  （`created` / `reused` / `skipped` / `refused`）、简报过期的 N，全部照服务给的数写出来。
- §6.1 的两个数字（另有 N 道不能自动判定／另有 M 道缺擦除图）显示在页头，
  并**标出数的是哪一堆题**（`data-basis` / `data-basis-text`）；N 必须逐个理由列出道数。
- 出错误时说清「读不到什么、怎么办、错误码是什么」，并给一个重试按钮。**不许 `catch {}`。**

## 重做页（#7）

`/redo?queue=<题目列表>&i=<第几题>`。**队列在 URL 里**，服务端不参与队列的维护
（契约 §4）：判完一道题的瞬间它进了冷却、从默认打印清单里消失，服务端现算的
「第 i+1 题」会漂到别处去。入口在清单页的「开始屏幕重做」按钮上。

五条验收与它们的落点：

| 验收 | 落点 | 坏输入怎么办 |
|---|---|---|
| 1 队列在 URL 里、可刷新、可开在另一个窗口；**越界明确失败** | `src/lib/redo.js` 的 `parseRedoQuery` / `resolveQueueItem` | `queue_missing` / `queue_empty` / `queue_malformed` / `index_missing` / `index_invalid` / `index_out_of_range` / `problem_not_in_index`，每一种都渲染成一个带 `data-error-code` 的面板——**绝不悄悄落到第一题、也绝不跳过查不到的题** |
| 2 题面 = 擦除手写后的图（`images.clean`） | `problemImage` / `RedoQuestion` | 缺图渲染 `data-missing-clean="true"`；**没有一处回退到 `images.original`** |
| 3 输入随题型分化 | `answerMode` / `RedoQuestion` | 选择→选项、填空→一个空、**解答→不给作答框**（`data-answer-mode="none"`）；判据取自服务的 `auto_judge` 读数，题型枚举认不出也不退回选择题 |
| 4 判完立刻进冷却并从队列消失 | `submitScreenAnswer` → `POST /api/attempt/<pid>`（#5） | 界面**只提交 `{channel:"screen", answer}`**；判定、冷却、`note` 全是服务的读数。成功后 `removeAt` 把这道题从 URL 队列拿掉（`history.replaceState`） |
| 5 页头「另有 N 道题不能自动判定」 | `redoHeader` / `RedoHeader` | N/M 从 `screen_redo.bases[<开关选的那一套>]` **取**，不重算；N 逐个理由列码与道数，M 照 `no_clean_image.message` 原话显示 |

「显示冷却中的题」开关在清单页：它只切换**取哪一套服务算好的总体**
（`bases.including_cooling`），队列与 N/M 一起换，一个数字都不重算。

`RedoPage` 的根节点带 `data-redo-page` / `data-queue` / `data-queue-size` / `data-queue-index`，
失败面板带 `data-error-code`，结果区带 `data-attempt-result` / `data-verdict` / `data-credit`
——`#8` 的界面不变量直接对着这些属性断言。

## 界面测试

纯逻辑（队列解析、越界失败、题型分化、缺擦除图、提交形状）住在
`src/lib/redo.js`，一行 React 都没有，所以能脱网、装不装依赖都能测：

```bash
node --test site/tests/            # 65 条（#7 的 26 条 + #8 的判据 39 条）
python3 -m pytest server/tests/test_site_redo_queue.py   # 12 条：用 node 探针跑同一份实现
```

`site/tests/probe-redo.mjs` 就是给 pytest 用的探针：规则只有一份实现，不在 Python 里抄第二遍。

## 界面不变量自检（#8，替代原型的 `--selftest`）

```bash
node site/tests/selftest.mjs       # 不靠浏览器、不读 data/、不写任何东西
```

它把「屏幕重做」的四条不变量钉成可跑的检查（**32 条**），判据吃的是**渲染出来的 HTML**：

| 不变量 | 判据 | 坏输入会报什么 |
|---|---|---|
| 1 题面只能是**擦除手写后**的图 | `checkQuestionImage` | `question_image_not_clean`（原图／掩膜／整页照片）、`question_image_missing`、`question_image_kind_mismatch` |
| 2 解答题（及未审核／无标准答案）不给作答框 | `checkAnswerBox` | `solution_has_answer_box`、`blocked_question_has_answer_box`、`answer_box_missing`、`choice_options_missing`、`answer_mode_mismatch` |
| 3 队列越界／缺题必须**明确失败** | `checkQueue` | `fell_back_to_the_first_problem`、`queue_failure_not_rendered`、`queue_failure_wrong_code`、`other_problem_rendered`、`spurious_queue_error` |
| 4 自检**不得有副作用** | `checkNoSideEffects` | `selftest_created_file` / `selftest_modified_file` / `selftest_deleted_file` |
| （附带）提交之前页面上不许有答案 | `checkNoAnswerLeak` | `answer_text_leaked`、`answer_revealed_before_submit` |

三个要点：

1. **HTML 从哪来**：`site/tests/ssr.mjs` 用 React 的 `renderToStaticMarkup` 渲**真组件**
   （`RedoQuestion` / `RedoPage` / `RedoHeader`）——不开浏览器、不联网、不起服务。
   JSX 就地过 `@babel/preset-react`（**不落盘、不建缓存**）；`RedoPage` 的
   `indexData` 是给它留的缝（有它就不 fetch），`@docusaurus/router` 用桩注入 `search`。
2. **报警能力每一趟都验**：`fixtures/broken.js` 是三个**故意做坏的组件**（题面退回原图、
   解答题给作答框、越界落到第一题），判据必须对它们响——判据哪天不响了，自检自己会失败。
   理由是验收记录里那句：「一个从没失败过的检查，和一个从没通过过的检查，同样不可信」
   （`docs/acceptance-log.md:253`）。
3. **退出码分两种失败**：`1` = 界面不变量失败，`2` = 这次没跑完（**环境**缺依赖）。
   环境故障从不冒充界面故障——原型把两者混成一句「界面自检未通过」，人被引去修界面。

自检**不读 `data/`**（夹具在 `fixtures/problems.mjs` 里手写），所以它在 worktree 里天然跑得起来；
它还会把自己**前后两张目录快照**比一遍，确认自己真的没写任何东西（原型跑一次
`--selftest` 就生成了 `data/index.json`）。

pytest 侧由 `server/tests/test_ui_invariants.py` 驱动同一批判据（探针
`site/tests/probe-invariants.mjs`），并且在**真跑子进程的前后**拍快照验副作用。
真渲染那几条要 `site/node_modules`；没装会 skip 并说明原因。

## U0 里**没有**的东西（别以为坏了）

- 判定映射（#5/#6 的后端）、审核页、上传页——分别是 #5/#6/#9/#13 的事。
- 组件级 DOM 测试（jsdom 之类）仍然没有，也**不需要**：界面不变量的验收方式
  是上面那份自检（真渲染成 HTML → 判据）；#7 的验收证据是**真实渲染**（见下）。

## 验收证据怎么复现

```bash
python3 -m server.app --data ../data --port 8765 &
AI_NOTE_API=http://127.0.0.1:8765 npm run start -- --port 3000 --host 127.0.0.1 &
chromium --headless=new --no-sandbox --user-data-dir=/tmp/c --virtual-time-budget=20000 \
  --dump-dom http://127.0.0.1:3000/ | grep -o 'data-problem-id="[^"]*"'
```

数据目录里那 2 张卡会以 `data-problem-id="…"` 出现，一张 `data-channel="screen"`、
一张 `data-channel="paper"`，两张各带一张 `data-image-kind="clean"` 的图。

重做页的渲染断言（造 4 张夹具卡：选择题／填空题／解答题／缺擦除图，走真服务）：

```bash
chromium --headless=new --no-sandbox --user-data-dir=/tmp/c --virtual-time-budget=9000 \
  --dump-dom 'http://127.0.0.1:3000/redo/?queue=p-choice01,p-fill02&i=2' \
  | grep -o 'data-error-code="[a-z_]*"'          # → index_out_of_range
```

`.verify/` 里留着一次性脚本（该目录已 gitignore、不进仓库）。#7 那一趟的三个
（`dom-check.sh` 的 33 条 DOM 断言、`submit-check.mjs`、`toggle-check.mjs`）在后续的
工作区清理里没了；**#17 这一趟重新留了三个**：

```bash
node .verify/frontend/cdp-check.mjs 'http://127.0.0.1:3100/split/?page=<页 id>'
#   点一块已绑卡的块 → 删它（服务的原话 + block_delete_bound_to_card）
#   → 重跑切分（409 的 details.discarded / 或服务原话的失败面板）→ 入库（四类结果逐条）
node .verify/frontend/cdp-pick.mjs 'http://127.0.0.1:3100/'
#   首页点「上传」→ 工作台那一屏（file input 的 accept/capture、拖拽区、科目选项）
```

