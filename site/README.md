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

## U0 里**没有**的东西（别以为坏了）

- 「显示冷却中的题」开关、重做页、判定、审核页、上传页——分别是 #7/#9/#13 的事。
  服务已经把两套候选总体的读数都算好了（`data.screen_redo.bases`），
  做开关时**取**数字，不要重算。
- 前端还没有自动化测试。界面不变量的验收方式（自检不迁移的原型 → 重建的界面测试）
  归 #8；本里程碑的验收证据是**真实渲染**（见下）。

## 验收证据怎么复现

```bash
python3 -m server.app --data ../data --port 8765 &
AI_NOTE_API=http://127.0.0.1:8765 npm run start -- --port 3000 --host 127.0.0.1 &
chromium --headless=new --no-sandbox --user-data-dir=/tmp/c --virtual-time-budget=20000 \
  --dump-dom http://127.0.0.1:3000/ | grep -o 'data-problem-id="[^"]*"'
```

数据目录里那 2 张卡会以 `data-problem-id="…"` 出现，一张 `data-channel="screen"`、
一张 `data-channel="paper"`，两张各带一张 `data-image-kind="clean"` 的图。
