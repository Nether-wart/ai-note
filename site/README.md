# `site/` —— 界面壳（Docusaurus，ADR 0006）

清单页的壳。**它不碰数据、不碰模型、不碰状态机**：只把 `server/` 给的 JSON 画出来。

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

1. **数据不进构建产物**（ADR 0001）。清单页在 `useEffect` 里 fetch，
   构建期只渲染「正在从服务读索引…」。所以录入一道题**不会**触发站点重建，
   构建产物里也没有任何一道题的内容。
   自查：`grep -rl "p-2026" build/` 必须为空（作者验过一次，为空）。
2. **Tauri 兼容**（ADR 0006）：纯静态 bundle、不做 SSR、无服务端渲染依赖。

## 界面纪律

- **只显示擦除手写后的题面图**（`images.clean`）。没有就明说「没有擦除图，也不回退到原图」，
  绝不回退到 `images.original`——原图印着订正，把答案摆在面前的重做没有意义（裁决 D4）。
  原图端点存在且可用，但**目前没有任何界面在用它**。
- **每题都标通道**（屏幕可做／只能纸上 + 第一条阻塞原话）。清单里少一道题看起来就像 bug。
- **清单页不显示标准答案、正解、原答与订正**：它不是审核页，没有理由把答案印在屏幕上。
- **`warnings[]` 与 `skipped[]` 必须显示**，服务给的 `message` **照原话显示**，
  界面不自己再拼一遍（拼了就会出现两句几乎一样的话——里程碑二第一次真实渲染就撞上了）。
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
node --test site/tests/            # 26 条
python3 -m pytest server/tests/test_site_redo_queue.py   # 12 条：用 node 探针跑同一份实现
```

`site/tests/probe-redo.mjs` 就是给 pytest 用的探针：规则只有一份实现，不在 Python 里抄第二遍。

## U0 里**没有**的东西（别以为坏了）

- 判定映射（#5/#6 的后端）、审核页、上传页——分别是 #5/#6/#9/#13 的事。
- **前端还没有组件级测试**（没有 jsdom 之类的依赖）：界面不变量的验收方式
  （自检不迁移的原型 → 重建的界面测试）归 #8；#7 的验收证据是**真实渲染**（见下）。

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

`.verify/` 里留着这一次的一次性脚本（该目录已 gitignore、不进仓库）：`dom-check.sh`（33 条 DOM 断言）、
`submit-check.mjs`（CDP 真点一次作答 → 判定 → 队列少一道）、`toggle-check.mjs`（开关换总体）。

